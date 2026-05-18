import re


DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"


def normalize(value: str) -> str:
    return re.sub(r"[\s\-.]", "", value).upper()


def is_valid_dni(value: str) -> bool:
    value = normalize(value)

    if not re.fullmatch(r"\d{8}[A-Z]", value):
        return False

    number = int(value[:8])
    expected = DNI_LETTERS[number % 23]

    return value[-1] == expected


def is_valid_nie(value: str) -> bool:
    value = normalize(value)

    if not re.fullmatch(r"[XYZ]\d{7}[A-Z]", value):
        return False

    prefix_map = {"X": "0", "Y": "1", "Z": "2"}
    dni_equivalent = prefix_map[value[0]] + value[1:]

    return is_valid_dni(dni_equivalent)


def iban_checksum_valid(iban: str) -> bool:
    iban = normalize(iban)

    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", iban):
        return False

    rearranged = iban[4:] + iban[:4]
    numeric = ""

    for char in rearranged:
        if char.isdigit():
            numeric += char
        else:
            numeric += str(ord(char) - 55)

    return int(numeric) % 97 == 1


def is_valid_spanish_iban(value: str) -> bool:
    value = normalize(value)
    return value.startswith("ES") and iban_checksum_valid(value)


def is_valid_cif(value: str) -> bool:
    value = normalize(value)

    if not re.fullmatch(r"[ABCDEFGHJNPQRSUVW]\d{7}[0-9A-J]", value):
        return False

    first = value[0]
    digits = value[1:8]
    control = value[8]

    odd_sum = 0
    even_sum = 0

    for index, char in enumerate(digits, start=1):
        digit = int(char)

        if index % 2 == 1:
            doubled = digit * 2
            odd_sum += doubled // 10 + doubled % 10
        else:
            even_sum += digit

    total = odd_sum + even_sum
    control_digit = (10 - (total % 10)) % 10
    control_letter = "JABCDEFGHI"[control_digit]

    if first in "ABEH":
        return control == str(control_digit)

    if first in "KPQS":
        return control == control_letter

    return control == str(control_digit) or control == control_letter


def looks_like_cadastral_reference(value: str) -> bool:
    value = normalize(value)

    # Formato urbano habitual de 20 caracteres.
    # No hacemos todavía checksum oficial; lo tratamos como validación de formato.
    return bool(re.fullmatch(r"\d{7}[A-Z]{2}\d{4}[A-Z]\d{4}[A-Z]{2}", value))