"""Print Webtop grades for the current school year, signing in with Ministry credentials.

    python examples/webtop_grades.py            # Ministry of Education account
    python examples/webtop_grades.py --webtop   # Webtop's own username/password
"""

import argparse
import getpass

from scholix.errors import AuthenticationError, CaptchaRequired, VerificationRequired
from scholix.webtop import WebtopClient


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--webtop", action="store_true", help="use a Webtop account instead of a Ministry one")
    args = parser.parse_args()

    client = WebtopClient()
    user, password = input("User name: "), getpass.getpass("Password: ")
    try:
        (client.login_password if args.webtop else client.login_moe)(user, password)
    except CaptchaRequired:
        raise SystemExit("The Ministry demands a CAPTCHA; sign in on its website first, then try again.")
    except VerificationRequired as error:
        raise SystemExit(f"More verification is needed: {error}")
    except AuthenticationError as error:
        raise SystemExit(f"Sign-in failed: {error}")

    print(f"Signed in as {client.name}")
    for grade in client.grades(semester="ab"):
        print(f"{grade.date[:10]:10}  {grade.subject:24}  {grade.name:30}  {grade.value}")


if __name__ == "__main__":
    main()
