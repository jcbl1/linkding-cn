"""
Reader script - replaces defuddle engine.

Parameters:
  url: str - URL being processed
  config: dict - merged config (default + reader sections)

Return:
  str (HTML content) or dict with content and optional title
"""


def extract(url, config, output_path=None):
    # Access config fields
    defuddle_args = config.get("defuddle_args", {})
    headers = config.get("headers", {})

    # TODO: implement your reader logic
    # Example using BeautifulSoup:
    # from bs4 import BeautifulSoup
    # import requests
    # resp = requests.get(url, headers=headers)
    # soup = BeautifulSoup(resp.text, "html.parser")
    # article = soup.find("article")
    # return str(article) if article else ""

    return ""
