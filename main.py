
from src.config import settings


def main():
    print(settings.stripe_llms_url)
    print(settings.download_concurrency)


if __name__ == "__main__":
    main()
