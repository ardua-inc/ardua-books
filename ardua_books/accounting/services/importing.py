from decimal import Decimal


def normalize_amount(raw_amount: Decimal, profile):
    """
    Convert CSV amount into the internal Ardua Books convention:
    + = payment/deposit
    - = charge/withdrawal
    """

    rule = profile.sign_rule

    if rule == "BANK_STANDARD":
        # Bank accounts: CSV convention matches internal convention
        return raw_amount

    elif rule == "CC_CHARGES_POSITIVE":
        # CSV: + charge, - payment
        # Internal: + payment, - charge
        return -raw_amount

    elif rule == "CC_CHARGES_NEGATIVE":
        # CSV: + payment, - charge
        return raw_amount

    return raw_amount
