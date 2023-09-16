import json
from http import HTTPStatus
from typing import TYPE_CHECKING, Iterator, Protocol, Unpack, final

import httpretty
import pytest
from django.conf import settings
from django.db import models
from django.test import Client
from django.urls import reverse
from mimesis import Field, Locale, Schema

from server.apps.identity.models import User

if TYPE_CHECKING:
    from tests.plugins.identity.user import (
        ExternalAPIUserResponse,
        RegistrationData,
        UserAssertion,
        UserData,
    )


@final
class RegistrationDataFactory(Protocol):
    def __call__(self, **fields: Unpack['RegistrationData']) -> 'RegistrationData':
        """Just a protocol."""


@pytest.fixture()
def registration_data_factory() -> RegistrationDataFactory:
    def factory(**fields: Unpack['RegistrationData']) -> 'RegistrationData':
        field = Field(locale=Locale.RU)
        password = field('password')
        schema = Schema(schema=lambda: {
            'email': field('person.email', domains=['picapp.com']),
            'first_name': field('person.first_name'),
            'last_name': field('person.last_name'),
            'date_of_birth': field('datetime.date'),
            'address': field('address.city'),
            'job_title': field('person.occupation'),
            'phone': field('person.telephone'),
        })
        return {
            **schema.create()[0],
            **{'password1': password, 'password2': password},
            **fields,
        }

    return factory


@pytest.fixture(scope='session')
def assert_correct_user() -> 'UserAssertion':
    def factory(email: str, expected: 'UserData'):
        user = User.objects.get(email=email)
        assert user.is_active
        assert not user.is_staff
        assert not user.is_superuser
        for field_name, field_value in expected.items():
            assert getattr(user, field_name) == field_value
    return factory


@pytest.fixture()
def registration_data(registration_data_factory: RegistrationDataFactory) -> 'RegistrationData':
    return registration_data_factory()


@pytest.fixture()
def user_data(registration_data: 'RegistrationData') -> 'UserData':
    return {
        key: val
        for key, val in registration_data.items()  # noqa: WPS110
        if not key.startswith('password')
    }


@pytest.fixture()
def user(django_user_model: models.Model):
    return django_user_model.objects.create_user(  # noqa: S106
        email='user1@example.com', password='password1',
    )


@pytest.fixture()
def user_client(client: Client, user) -> Client:
    client.force_login(user)
    return client


@pytest.fixture()
def external_api_user_response() -> 'ExternalAPIUserResponse':
    field = Field()
    schema = Schema(schema=lambda: {
        'external_id': str(field('numeric.increment')),
    })
    return schema.create()[0]


@pytest.fixture()
@httpretty.activate
def external_api_mock(
    external_api_user_response: 'ExternalAPIUserResponse',
) -> Iterator['ExternalAPIUserResponse']:
    httpretty.register_uri(
        method=httpretty.POST,
        body=json.dumps(external_api_user_response),
        uri=settings.PLACEHOLDER_API_URL,
    )
    yield external_api_user_response
    assert httpretty.last_request()


@pytest.mark.django_db()
def test_anonymous_can_open_login_page(client: Client):
    response = client.get(reverse('identity:login'))

    assert response.status_code == HTTPStatus.OK
    assert response.get('Content-Type').startswith('text/html')


def test_user_is_redirected_to_dashboard_from_login_page(user_client: Client):
    response = user_client.get(reverse('identity:login'))

    assert response.status_code == HTTPStatus.FOUND
    assert response.get('Location') == reverse('pictures:dashboard')


def test_user_is_redirected_to_main_page_on_logout(user_client: Client):
    response = user_client.post(reverse('identity:logout'))

    assert response.status_code == HTTPStatus.FOUND
    assert response.get('Location') == reverse('index')


def test_anonymous_is_redirected_to_login_from_update_user_page(client: Client):
    response = client.get(reverse('identity:user_update'))

    assert response.status_code == HTTPStatus.FOUND
    assert response.get('Location').startswith(reverse('identity:login'))


def test_anonymous_cannot_sent_update_user_request(client: Client):
    response = client.post(reverse('identity:user_update'), data={})

    assert response.status_code == HTTPStatus.FOUND
    assert response.get('Location').startswith(reverse('identity:login'))


def test_user_can_open_user_update_page(user_client: Client):
    response = user_client.get(reverse('identity:user_update'))

    assert response.status_code == HTTPStatus.OK


def test_user_can_update_their_info(
    user_client: Client,
    user: User,
    external_api_mock: 'ExternalAPIUserResponse',
):
    user_data = {
        'first_name': 'new first name',
        'last_name': 'new last name',
        'date_of_birth': '01.01.1970',
        'address': 'new address',
        'job_title': 'new job title',
        'phone': 'new phone number',
    }

    response = user_client.post(reverse('identity:user_update'), data=user_data)

    assert response.status_code == HTTPStatus.FOUND
    assert response.get('Location') == reverse('identity:user_update')
    user.refresh_from_db()
    assert user.first_name == user_data['first_name']
    assert user.last_name == user_data['last_name']


@pytest.mark.django_db()
def test_registration_with_all_valid_fields(
    client: Client,
    registration_data: 'RegistrationData',
    user_data: 'UserData',
    assert_correct_user: 'UserAssertion',
    external_api_mock: 'ExternalAPIUserResponse',
):
    response = client.post(reverse('identity:registration'), data=registration_data)

    assert response.status_code == HTTPStatus.FOUND
    assert response.get('Location') == reverse('identity:login')
    assert_correct_user(registration_data['email'], user_data)


@pytest.mark.django_db()
@pytest.mark.parametrize('field', [f for f in User.REQUIRED_FIELDS if f != 'date_of_birth'] + [User.USERNAME_FIELD])  # noqa: WPS111,WPS221
def test_registration_fails_if_a_field_is_missing(
    client: Client,
    registration_data_factory: 'RegistrationDataFactory',
    field: str,
):
    registration_data = registration_data_factory(**{field: ''})

    response = client.post(reverse('identity:registration'), data=registration_data)

    assert response.status_code == HTTPStatus.OK
    assert not User.objects.filter(email=registration_data['email']).exists()
