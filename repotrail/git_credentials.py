"""Git credential helper. Credentials arrive exclusively through the child environment."""
import os
import sys


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "get":
        for line in sys.stdin:
            if not line.strip():
                break
        token = os.environ.get("REPOTRAIL_GIT_TOKEN", "")
        if token and not any(character in token for character in "\r\n"):
            print("username=oauth2")
            print("password=" + token)


if __name__ == "__main__":
    main()
