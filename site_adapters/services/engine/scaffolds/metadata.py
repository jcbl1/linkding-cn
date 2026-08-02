"""
Metadata script - replaces default extraction engine.

Parameters:
  url: str - URL being processed
  config: dict - merged config (default + metadata sections)
  html_content: str - fetched HTML content

Return:
  dict with title, description, image (any can be None)
"""


def extract(url, config, html_content=None):
    # Access config fields
    select_title = config.get("select_title", [])
    select_description = config.get("select_description", [])
    select_image = config.get("select_image", [])

    # TODO: implement your extraction logic
    # Example using BeautifulSoup:
    # from bs4 import BeautifulSoup
    # soup = BeautifulSoup(html_content, "html.parser")
    # title = soup.select_one(select_title[0]).text if select_title else None

    return {
        "title": None,
        "description": None,
        "image": None,
    }
