"""Print your Mashov grades and this week's timetable.

    python examples/mashov_grades.py

Asks for the school, user name and password interactively; nothing is stored.
"""

import getpass

from scholix.errors import AuthenticationError
from scholix.mashov import MashovClient, current_school_year, list_schools

DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def main() -> None:
    needle = input("Part of your school's name: ").strip()
    matches = [s for s in list_schools() if needle in s["name"]]
    if not matches:
        raise SystemExit("No school matches that text")
    for index, school in enumerate(matches[:20]):
        print(f"{index:2}  {school['semel']}  {school['name']}")
    school = matches[int(input("Choose a number: "))]

    client = MashovClient(school["semel"], input("User name: "), year=current_school_year())
    try:
        client.login(getpass.getpass("Password: "))
    except AuthenticationError as error:
        raise SystemExit(f"Sign-in failed: {error}")

    print(f"\nSigned in as {client.display_name}\n")
    for grade in client.grades():
        print(f"{grade.date[:10]:10}  {grade.subject:24}  {grade.name:24}  {grade.value}")

    print()
    for lesson in sorted(client.timetable(), key=lambda l: (l.day, l.period or 0)):
        print(f"{DAYS[lesson.day]}  #{lesson.period}  {lesson.start:5}-{lesson.end:5}  {lesson.subject}  {lesson.room}")

    print("\nKeep client.device_pass to sign in again without a password.")


if __name__ == "__main__":
    main()
