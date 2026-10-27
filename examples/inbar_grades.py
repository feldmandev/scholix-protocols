"""Sign in to Inbar with one SMS code and print grades for every available year.

    python examples/inbar_grades.py

This sends exactly one SMS. If Inbar refuses (it can, with no stated expiry), the
script stops instead of retrying.
"""

import getpass

from scholix.errors import AuthenticationError, RateLimited
from scholix.inbar import InbarClient


def main() -> None:
    client = InbarClient()
    try:
        client.request_sms(input("ID / passport: ").strip(), input("Registered mobile: ").strip())
        first = client.verify_sms(getpass.getpass("Code from the SMS: ").strip())
    except RateLimited as error:
        raise SystemExit(f"Inbar is refusing SMS right now: {error}")
    except AuthenticationError as error:
        raise SystemExit(f"Sign-in failed: {error}")

    for year in first.years:
        page = first if year == first.year else client.grades(year)
        print(f"\n== {year} ==")
        for course in page.courses:
            final = next((g.value for g in course.grades if g.type == "final"), "pending")
            print(f"{course.name:40} {course.credits:>4} credits   final: {final}")


if __name__ == "__main__":
    main()
