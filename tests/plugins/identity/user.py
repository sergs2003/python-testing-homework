import datetime as dt
from typing import Callable, TypeAlias, TypedDict, final


class UserData(TypedDict, total=True):
    email: str
    first_name: str
    last_name: str
    date_of_birth: dt.date
    address: str
    job_title: str
    phone: str


@final
class RegistrationData(UserData, total=True):
    password1: str
    password2: str


UserAssertion: TypeAlias = Callable[[str, UserData], None]


@final
class ExternalAPIUserResponse(TypedDict, total=True):
    external_id: str
