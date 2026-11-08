"""Demographic rules for the new-case registration contract."""

import unicodedata


def normalize_name(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Name must be text.")
    name = unicodedata.normalize("NFC", value).strip(" ")
    if (
        not 1 <= len(name) <= 100
        or not any(character.isalpha() for character in name)
        or not all(character.isalpha() or character == " " for character in name)
    ):
        raise ValueError("Name must contain only letters and ordinary spaces.")
    return name


def age_at_registration(birth_year: object, registration_year: int) -> int:
    if type(birth_year) is not int:
        raise ValueError("Birth year must be an integer.")
    age = registration_year - birth_year
    if not 18 <= age <= 100:
        raise ValueError("Age at registration must be between 18 and 100.")
    return age
