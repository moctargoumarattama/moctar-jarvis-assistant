import os

import googlemaps


def get_google_maps_api_key():
    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GOOGLE_MAPS_API_KEY is missing.")
    return api_key


def get_directions(api_key, origin, destination, mode="driving", departure_time=None):
    gmaps = googlemaps.Client(key=api_key)
    return gmaps.directions(
        origin,
        destination,
        mode=mode,
        departure_time=departure_time,
    )


def main():
    api_key = get_google_maps_api_key()
    origin = input("Enter the starting location: ")
    destination = input("Enter the destination: ")

    directions = get_directions(api_key, origin, destination)

    print("Directions:")
    for step in directions[0]["legs"][0]["steps"]:
        print(step["html_instructions"])


if __name__ == "__main__":
    main()
