"""Cheapest Trip Finder: a Drafter website that shows the cheapest live flights for a trip.

The first page asks for an Ignav API key. After the key is entered, the rest of the site opens,
and the key is remembered (in the State) so it is never asked for again, even on a new search.
The user types where they are flying from and to, where they are staying (it can be a different
place than the airport, like flying into Denver but staying in Breckenridge), their dates, and a
few choices. The site shows live flight prices from the Ignav flight API. The trip total page then
shows a link to a live Booking.com search for the place to stay, with the place and dates already
filled in, for the user to copy into a new browser tab.
Flights are live. The places to stay shown on the results and trip total pages are made-up
EXAMPLES, not real hotels or real prices.

Advanced techniques used (not covered in class, so documented here):
- sorted(items, key=helper) sorts a list by whatever number the helper function returns.
- try / except catches an error so the site can keep working instead of crashing. It is used
  around the live flight lookup, because that can fail (no internet, wrong key, and so on).
- "import requests" is written inside a function (instead of at the top of the file) so that the
  site still starts if requests is missing. The requests library sends the question to Ignav
  over the internet (requests.post) and gives back its answer (response.json()).
- dict.get(key, default) reads a value from a dictionary and gives the default if it is missing.
  Ignav's answer is JSON, which Python turns into dictionaries and lists.
- "value or default" gives back value, unless value is missing (None), empty ("", [], {}), or 0;
  then it gives back default. For example, itinerary.get("outbound") or {} gives an empty
  dictionary when Ignav sent no outbound part, and segment.get("duration_minutes") or 0 gives 0.
- "if value:" (with no comparison) treats None, "", 0, and empty lists or dictionaries as False
  and anything else as True. For example, "if carrier:" means "if Ignav gave a carrier name".
- list[:10] (slicing) keeps only the first 10 items of a list.
- text.encode("utf-8") turns text into bytes (numbers from 0 to 255). A letter like the u with
  dots in Zurich becomes two bytes. chr(number) turns a byte back into a letter, and hex(number)
  writes a number in base 16. They are used to turn symbols and accented letters into %XX codes
  so a place name can be put safely inside a web link.
- A live Booking.com web address is built from the user's place and dates (text is encoded
  for a link) and shown on the page to copy, because Drafter links and buttons only go to
  pages inside the site.
- hide_debug_information() is a Drafter function that hides the box Drafter can show at the
  bottom of each page with the State in it. It is called inside try / except so that nothing
  breaks if a version of Drafter does not have it. This keeps the API key off the screen.
- A Cloudflare Worker (a tiny program on Cloudflare's servers, written in JavaScript, not in this
  file) sits between this site and Ignav. It only passes the question and the key along, because
  a web browser will not let this page call Ignav directly.
"""
from dataclasses import dataclass
from drafter import *
from bakery import assert_equal

# Keep the call that came with your project template if it looks different from this one.
set_site_information(
    author="Connor Growney",
    description="Enter a trip and see the cheapest live flights, then search for a place to stay.",
    sources=["Drafter documentation", "Ignav flight API documentation",
             "Claude AI (conversation uploaded to the repository)"],
    planning="planning.pdf",
    links=["https://github.com/cgrowney1/cheapest-trip-finder"],
)

try:
    hide_debug_information()
except Exception:
    pass

# Global constants (they never change while the site runs).
# The site sends its flight questions to this Cloudflare Worker, which passes them on to Ignav.
WORKER_URL = "https://withered-wood-9da2.connorgrowney1.workers.dev"
FLIGHTS_URL = WORKER_URL + "/api/fares"
BOOKING_SEARCH_URL = "https://www.booking.com/searchresults.html"
MIN_KEY_LENGTH = 10
MAX_FLIGHTS_SHOWN = 10
SAFE_LINK_CHARACTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~"
DAYS_IN_MONTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
ONE_WAY_NOTE = " (one way: 1 night shown)"


@dataclass
class Flight:
    """One flight option found by a search."""
    price: float
    currency: str
    airline: str
    duration_minutes: int
    stops: int
    depart_time: str
    arrive_time: str


@dataclass
class ExampleStay:
    """One EXAMPLE place to stay. These are made-up examples, not real hotels or live prices."""
    name: str
    price_per_night: int
    rating: float


@dataclass
class FlightSearch:
    """What a flight lookup found: the flights, a message, and how it went.

    status is "ok" (the lookup worked), "bad_key" (Ignav refused the key), or "failed"
    (the lookup did not work). The message explains a problem and is "" when status is "ok".
    """
    flights: list[Flight]
    message: str
    status: str


@dataclass
class State:
    """Everything the site remembers between pages."""
    api_key: str
    origin: str
    destination: str
    stay_location: str
    depart_date: str
    return_date: str
    nonstop_only: bool
    sort_by: str
    flights: list[Flight]
    error_message: str


# ---------------------------------------------------------------------------
# Helpers for checking the form
# ---------------------------------------------------------------------------

def is_leap_year(year: int) -> bool:
    """Return True if the given year is a leap year."""
    return (year % 4 == 0 and year % 100 != 0) or year % 400 == 0


def days_in_month(year: int, month: int) -> int:
    """Return how many days are in the given month (1 to 12) of the given year."""
    if month == 2 and is_leap_year(year):
        return 29
    return DAYS_IN_MONTH[month - 1]


def is_valid_date(text: str) -> bool:
    """Return True if text is a real date written like 2026-12-20."""
    parts = text.split("-")
    if len(parts) != 3:
        return False
    for part in parts:
        if not part.isdigit():
            return False
    if len(parts[0]) != 4 or len(parts[1]) != 2 or len(parts[2]) != 2:
        return False
    year = int(parts[0])
    month = int(parts[1])
    day = int(parts[2])
    if month < 1 or month > 12:
        return False
    return day >= 1 and day <= days_in_month(year, month)


def date_to_day_number(text: str) -> int:
    """Turn a valid date like 2026-12-20 into a day count so two dates can be compared."""
    parts = text.split("-")
    year = int(parts[0])
    month = int(parts[1])
    day = int(parts[2])
    earlier_years = year - 1
    total = earlier_years * 365 + earlier_years // 4 - earlier_years // 100 + earlier_years // 400
    for earlier_month in range(1, month):
        total += days_in_month(year, earlier_month)
    return total + day


def count_nights(depart_date: str, return_date: str) -> int:
    """Return the nights between two valid dates, or 0 when there is no return date."""
    if return_date == "":
        return 0
    return date_to_day_number(return_date) - date_to_day_number(depart_date)


def is_valid_airport(code: str) -> bool:
    """Return True if code is exactly 3 letters, like PHL."""
    return len(code) == 3 and code.isalpha()


def check_input(origin: str, destination: str, depart_date: str, return_date: str) -> str:
    """Return a message describing the first problem with the form, or "" if it is fine."""
    if not is_valid_airport(origin) or not is_valid_airport(destination):
        return "Airport codes must be 3 letters, like PHL or LAX."
    if origin == destination:
        return "Please choose two different airports."
    if not is_valid_date(depart_date):
        return "The departure date must look like 2026-12-20."
    if return_date != "" and not is_valid_date(return_date):
        return "The return date must look like 2026-12-27, or be left blank."
    if return_date != "" and date_to_day_number(return_date) < date_to_day_number(depart_date):
        return "The return date cannot be before the departure date."
    return ""


def check_key(key: str) -> str:
    """Return a message describing the problem with a typed API key, or "" if it looks fine."""
    if key == "":
        return "Please enter your Ignav API key to continue."
    if " " in key or len(key) < MIN_KEY_LENGTH:
        return "That does not look like a whole API key. Paste it with no spaces."
    return ""


def is_checked(value: object) -> bool:
    """Return True if a CheckBox value means checked (it may arrive as True or as "True")."""
    return str(value).lower() == "true"


# ---------------------------------------------------------------------------
# Places to stay: a live Booking.com search with the place and dates filled in
# ---------------------------------------------------------------------------

def stay_place(stay_location: str, destination: str) -> str:
    """Return where to look for a place to stay: the typed place, or the arrival airport."""
    if stay_location != "":
        return stay_location
    return destination + " airport"


def make_example_stays(place: str) -> list[ExampleStay]:
    """Return made-up EXAMPLE stays near a place, so the page has something to show.

    These are not real hotels and not live prices.
    """
    return [ExampleStay("Budget Inn near " + place, 79, 3.8),
            ExampleStay("Hostel near " + place, 45, 4.0),
            ExampleStay("Hotel near " + place, 129, 4.3),
            ExampleStay("Resort near " + place, 219, 4.6)]


def encode_for_link(text: str) -> str:
    """Make text safe to put in a web link: spaces become + and everything else that is not
    a plain letter, digit, or - _ . ~ becomes %XX codes (accented letters are kept this way)."""
    encoded = ""
    for byte in text.encode("utf-8"):
        letter = chr(byte)
        if letter in SAFE_LINK_CHARACTERS:
            encoded += letter
        elif letter == " ":
            encoded += "+"
        else:
            code = hex(byte)[2:].upper()
            if len(code) < 2:
                code = "0" + code
            encoded += "%" + code
    return encoded


def booking_link(place: str, check_in: str, check_out: str) -> str:
    """Return a Booking.com search link for a place, with the dates filled in if there are any."""
    link = BOOKING_SEARCH_URL + "?ss=" + encode_for_link(place)
    if check_out != "":
        link += "&checkin=" + check_in + "&checkout=" + check_out
    return link + "&group_adults=1&no_rooms=1&order=price"


# ---------------------------------------------------------------------------
# Live flights from the Ignav API
# ---------------------------------------------------------------------------

def clock_from_timestamp(text: str) -> str:
    """Turn a time like 2026-12-20T08:05:00 into 08:05 (other text is returned unchanged)."""
    if "T" in text and len(text) >= 16:
        return text[11:16]
    return text


def leg_airline(leg: dict) -> str:
    """Return the airline name of a flight leg from Ignav's answer."""
    carrier = leg.get("carrier")
    if carrier:
        return carrier
    segments = leg.get("segments", [])
    if len(segments) > 0 and segments[0].get("operating_carrier_name"):
        return segments[0]["operating_carrier_name"]
    return "Unknown airline"


def leg_minutes(leg: dict) -> int:
    """Return the travel time of a leg, adding up its parts if Ignav gave no total."""
    total = leg.get("duration_minutes")
    if total:
        return int(total)
    minutes = 0
    for segment in leg.get("segments", []):
        minutes += int(segment.get("duration_minutes") or 0)
    return minutes


def flight_from_itinerary(itinerary: dict) -> Flight:
    """Turn one itinerary from Ignav's answer into a Flight (it must have at least 1 segment)."""
    outbound = itinerary["outbound"]
    segments = outbound["segments"]
    price = itinerary["price"]
    return Flight(float(price["amount"]), price.get("currency", "USD"), leg_airline(outbound),
                  leg_minutes(outbound), len(segments) - 1,
                  clock_from_timestamp(segments[0].get("departure_time_local", "")),
                  clock_from_timestamp(segments[-1].get("arrival_time_local", "")))


def parse_itineraries(itineraries: list[dict]) -> list[Flight]:
    """Turn Ignav's list of itineraries into Flights, skipping any with no flight parts."""
    flights = []
    for itinerary in itineraries:
        outbound = itinerary.get("outbound") or {}
        if len(outbound.get("segments", [])) > 0:
            flights.append(flight_from_itinerary(itinerary))
    return flights


def request_live_flights(api_key: str, origin: str, destination: str, depart_date: str,
                         return_date: str, nonstop_only: bool) -> FlightSearch:
    """Ask Ignav (through the Worker) for real flight prices and describe how it went.

    Not unit tested: it needs the internet and a real key.
    """
    import requests
    body = {"origin": origin, "destination": destination, "departure_date": depart_date}
    endpoint = FLIGHTS_URL + "/one-way"
    if return_date != "":
        body["return_date"] = return_date
        endpoint = FLIGHTS_URL + "/round-trip"
    if nonstop_only:
        body["max_stops"] = 0
    response = requests.post(endpoint, headers={"X-Api-Key": api_key}, json=body, timeout=30)
    if response.status_code == 401 or response.status_code == 403:
        return FlightSearch([], "Ignav did not accept that API key. Please enter it again.",
                            "bad_key")
    if response.status_code != 200:
        return FlightSearch([], "Ignav answered with status " + str(response.status_code)
                            + ". Please try again in a moment.", "failed")
    return FlightSearch(parse_itineraries(response.json().get("itineraries", [])), "", "ok")


def find_flights(api_key: str, origin: str, destination: str, depart_date: str,
                 return_date: str, nonstop_only: bool) -> FlightSearch:
    """Get live flights. If the lookup fails, say so (there are never made-up flights).

    Not unit tested: it needs the internet and a real key.
    """
    try:
        return request_live_flights(api_key, origin, destination, depart_date, return_date,
                                    nonstop_only)
    except Exception as problem:
        return FlightSearch([], "The live flight lookup failed: " + str(problem), "failed")


# ---------------------------------------------------------------------------
# Helpers for sorting, filtering, and showing results
# ---------------------------------------------------------------------------

def flight_price(flight: Flight) -> float:
    """Return a flight's price (used to sort flights)."""
    return flight.price


def flight_duration(flight: Flight) -> int:
    """Return a flight's travel time in minutes (used to sort flights)."""
    return flight.duration_minutes


def stay_price(stay: ExampleStay) -> int:
    """Return an example stay's price per night (used to sort stays)."""
    return stay.price_per_night


def only_nonstop(flights: list[Flight]) -> list[Flight]:
    """Return only the flights with no stops."""
    nonstop = []
    for flight in flights:
        if flight.stops == 0:
            nonstop.append(flight)
    return nonstop


def sort_flights(flights: list[Flight], sort_by: str) -> list[Flight]:
    """Return the flights sorted by "duration" or, for anything else, by price."""
    if sort_by == "duration":
        return sorted(flights, key=flight_duration)
    return sorted(flights, key=flight_price)


def find_cheapest_flight(flights: list[Flight]) -> Flight:
    """Return the cheapest flight in a list that has at least one flight."""
    cheapest = flights[0]
    for flight in flights:
        if flight.price < cheapest.price:
            cheapest = flight
    return cheapest


def format_money(amount: float) -> str:
    """Show an amount in whole dollars, like $215."""
    return "$" + str(int(round(amount)))


def describe_stops(stops: int) -> str:
    """Return Nonstop, 1 stop, or 2 stops."""
    if stops == 0:
        return "Nonstop"
    if stops == 1:
        return "1 stop"
    return str(stops) + " stops"


def describe_duration(minutes: int) -> str:
    """Turn minutes into text like 5h 30m."""
    return str(minutes // 60) + "h " + str(minutes % 60) + "m"


def stay_nights(state: State) -> int:
    """Return how many nights to price for the stay (at least 1)."""
    nights = count_nights(state.depart_date, state.return_date)
    if nights < 1:
        return 1
    return nights


def one_way_note(state: State) -> str:
    """Return a note saying 1 night is shown for a one way trip, or "" for a round trip."""
    if state.return_date == "":
        return ONE_WAY_NOTE
    return ""


def cheapest_stay(stays: list[ExampleStay]) -> ExampleStay:
    """Return the stay with the lowest price per night."""
    cheapest = stays[0]
    for stay in stays:
        if stay.price_per_night < cheapest.price_per_night:
            cheapest = stay
    return cheapest


def trip_type(state: State) -> str:
    """Return "round trip" or "one way" for the saved search."""
    if state.return_date == "":
        return "one way"
    return "round trip"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@route
def index(state: State) -> Page:
    """Show the first page, which asks for the API key (it is skipped once a key is saved)."""
    if state.api_key != "":
        return search_form(state)
    content = [
        Header("Cheapest Trip Finder", 1),
        "Enter your Ignav API key to start. You only have to enter it once.",
        LineBreak(),
        "You can get a free key at ignav.com/signup.",
        LineBreak(),
    ]
    if state.error_message != "":
        content.append(state.error_message)
        content.append(LineBreak())
        state.error_message = ""
    content.append("Ignav API key:")
    content.append(TextBox("api_key", ""))
    content.append(LineBreak())
    content.append(Button("Continue", save_key))
    return Page(state, content)


@route
def save_key(state: State, api_key: str) -> Page:
    """Check the typed key, save it in the state, and open the search page."""
    cleaned = api_key.strip()
    problem = check_key(cleaned)
    if problem != "":
        state.error_message = problem
        return index(state)
    state.api_key = cleaned
    state.error_message = ""
    return search_form(state)


@route
def forget_key(state: State) -> Page:
    """Remove the saved key (and any old error message) and go back to the key page."""
    state.api_key = ""
    state.error_message = ""
    return index(state)


@route
def search_form(state: State) -> Page:
    """Show the search form, filled in with the user's last search."""
    if state.api_key == "":
        return index(state)
    return Page(state, [
        Header("Cheapest Trip Finder", 1),
        "Find the cheapest flight for your trip, then search for a place to stay.",
        LineBreak(),
        "Departing airport (like PHL):",
        TextBox("origin", state.origin),
        LineBreak(),
        "Arrival airport (like DEN):",
        TextBox("destination", state.destination),
        LineBreak(),
        "Where are you staying? (like Breckenridge, CO. Leave blank to stay near the "
        "arrival airport):",
        TextBox("stay_location", state.stay_location),
        LineBreak(),
        "Departure date (YYYY-MM-DD):",
        TextBox("depart_date", state.depart_date),
        LineBreak(),
        "Return date (YYYY-MM-DD, leave blank for one way):",
        TextBox("return_date", state.return_date),
        LineBreak(),
        CheckBox("nonstop_only", state.nonstop_only),
        "Nonstop flights only",
        LineBreak(),
        "Sort flights by:",
        SelectBox("sort_by", ["price", "duration"], state.sort_by),
        LineBreak(),
        Button("Search", search_trip),
        Button("Change API key", forget_key),
    ])


@route
def search_trip(state: State, origin: str, destination: str, stay_location: str,
                depart_date: str, return_date: str, nonstop_only: bool, sort_by: str) -> Page:
    """Check the form, find live flights, save them in the state, and show the results."""
    if state.api_key == "":
        return index(state)
    state.origin = origin.strip().upper()
    state.destination = destination.strip().upper()
    state.stay_location = stay_location.strip()
    state.depart_date = depart_date.strip()
    state.return_date = return_date.strip()
    state.nonstop_only = is_checked(nonstop_only)
    state.sort_by = sort_by
    state.flights = []
    problem = check_input(state.origin, state.destination, state.depart_date, state.return_date)
    if problem != "":
        state.error_message = problem
        return error_page(state)
    search = find_flights(state.api_key, state.origin, state.destination, state.depart_date,
                          state.return_date, state.nonstop_only)
    if search.status == "bad_key":
        state.api_key = ""
        state.error_message = search.message
        return index(state)
    if search.status == "failed":
        state.error_message = search.message
        return error_page(state)
    flights = search.flights
    if state.nonstop_only:
        flights = only_nonstop(flights)
    state.flights = sort_flights(flights, state.sort_by)[:MAX_FLIGHTS_SHOWN]
    if len(state.flights) == 0:
        state.error_message = "No flights were found for those choices. Try different ones."
        return error_page(state)
    state.error_message = ""
    return results(state)


@route
def results(state: State) -> Page:
    """Show the cheapest flight, then every flight with a Details button."""
    if len(state.flights) == 0:
        return Page(state, ["There are no results yet.", Button("Start a search", search_form)])
    cheapest_flight = find_cheapest_flight(state.flights)
    content = [
        Header(state.origin + " to " + state.destination + " (" + trip_type(state) + ")", 1),
        "Live prices from Ignav.",
        LineBreak(),
        "Cheapest flight: " + format_money(cheapest_flight.price) + " on "
        + cheapest_flight.airline,
        LineBreak(),
        Header("Flights", 2),
    ]
    for position in range(len(state.flights)):
        flight = state.flights[position]
        content.append(format_money(flight.price) + " - " + flight.airline + " - "
                       + describe_stops(flight.stops) + " ")
        content.append(Button("Details", flight_details, [Argument("flight_number", position)]))
        content.append(LineBreak())
    place = stay_place(state.stay_location, state.destination)
    nights = stay_nights(state)
    content.append(Header("Places to stay near " + place, 2))
    for stay in sorted(make_example_stays(place), key=stay_price):
        content.append(stay.name + " - $" + str(stay.price_per_night) + "/night - about $"
                       + str(stay.price_per_night * nights) + " for " + str(nights)
                       + " night(s)" + one_way_note(state) + " - rating " + str(stay.rating))
        content.append(LineBreak())
    content.append(LineBreak())
    content.append(Button("See trip total", trip_total))
    content.append(Button("New search", search_form))
    return Page(state, content)


@route
def flight_details(state: State, flight_number: int) -> Page:
    """Show everything about one flight from the list (flight_number is its place in the list)."""
    position = int(flight_number)
    if position < 0 or position >= len(state.flights):
        state.error_message = "That flight could not be found."
        return error_page(state)
    flight = state.flights[position]
    return Page(state, [
        Header("Flight details", 1),
        Header(flight.airline, 2),
        "Price (" + trip_type(state) + "): " + format_money(flight.price) + " " + flight.currency,
        LineBreak(),
        "Stops: " + describe_stops(flight.stops),
        LineBreak(),
        "Travel time: " + describe_duration(flight.duration_minutes),
        LineBreak(),
        "Leaves " + state.origin + " at " + flight.depart_time + " on " + state.depart_date,
        LineBreak(),
        "Arrives in " + state.destination + " at " + flight.arrive_time,
        LineBreak(),
        Button("Back to results", results),
    ])


@route
def trip_total(state: State) -> Page:
    """Show the cheapest flight plus the cheapest stay, added up into a trip total."""
    if len(state.flights) == 0:
        return Page(state, ["There are no results yet.", Button("Start a search", search_form)])
    place = stay_place(state.stay_location, state.destination)
    nights = stay_nights(state)
    flight = find_cheapest_flight(state.flights)
    stay = cheapest_stay(make_example_stays(place))
    stay_total = stay.price_per_night * nights
    return Page(state, [
        Header("Trip total", 1),
        state.origin + " to " + state.destination + " (" + trip_type(state) + "), staying near "
        + place,
        LineBreak(),
        "Cheapest flight: " + format_money(flight.price) + " on " + flight.airline,
        LineBreak(),
        "Cheapest place to stay: " + stay.name + ", $" + str(stay.price_per_night) + "/night x "
        + str(nights) + " night(s) = $" + str(stay_total) + one_way_note(state),
        LineBreak(),
        Header("Total: " + format_money(flight.price + stay_total), 2),
        "To see real hotel prices, copy this address into a new browser tab:",
        LineBreak(),
        booking_link(place, state.depart_date, state.return_date),
        LineBreak(),
        Button("Back to results", results),
        Button("New search", search_form),
    ])


@route
def error_page(state: State) -> Page:
    """Show what went wrong and a button to try again."""
    return Page(state, [
        Header("Something went wrong", 1),
        state.error_message,
        LineBreak(),
        Button("Try again", search_form),
    ])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

TEST_KEY = "abcdefghij12"
DELTA_FLIGHT = Flight(215.0, "USD", "Delta", 300, 0, "08:05", "10:05")
UNITED_FLIGHT = Flight(180.0, "USD", "United", 420, 1, "06:00", "13:00")

# is_leap_year
assert_equal(is_leap_year(2024), True)
assert_equal(is_leap_year(2026), False)
assert_equal(is_leap_year(1900), False)
assert_equal(is_leap_year(2000), True)

# days_in_month
assert_equal(days_in_month(2024, 2), 29)
assert_equal(days_in_month(2026, 2), 28)
assert_equal(days_in_month(2026, 4), 30)
assert_equal(days_in_month(2026, 12), 31)

# is_valid_date
assert_equal(is_valid_date("2026-12-20"), True)
assert_equal(is_valid_date("2024-02-29"), True)
assert_equal(is_valid_date("2026-02-29"), False)
assert_equal(is_valid_date("2026-13-01"), False)
assert_equal(is_valid_date("2026-1-05"), False)
assert_equal(is_valid_date("hello"), False)
assert_equal(is_valid_date(""), False)

# date_to_day_number and count_nights
assert_equal(date_to_day_number("2026-12-21") - date_to_day_number("2026-12-20"), 1)
assert_equal(count_nights("2026-12-20", "2026-12-27"), 7)
assert_equal(count_nights("2026-12-30", "2027-01-02"), 3)
assert_equal(count_nights("2024-02-28", "2024-03-01"), 2)
assert_equal(count_nights("2026-12-20", ""), 0)

# is_valid_airport
assert_equal(is_valid_airport("PHL"), True)
assert_equal(is_valid_airport("PH1"), False)
assert_equal(is_valid_airport("PHLX"), False)
assert_equal(is_valid_airport(""), False)

# check_input
assert_equal(check_input("PHL", "DEN", "2026-12-20", "2026-12-27"), "")
assert_equal(check_input("PHL", "DEN", "2026-12-20", ""), "")
assert_equal(check_input("PH", "DEN", "2026-12-20", ""),
             "Airport codes must be 3 letters, like PHL or LAX.")
assert_equal(check_input("PHL", "PHL", "2026-12-20", ""), "Please choose two different airports.")
assert_equal(check_input("PHL", "DEN", "12/20/2026", ""),
             "The departure date must look like 2026-12-20.")
assert_equal(check_input("PHL", "DEN", "2026-12-20", "soon"),
             "The return date must look like 2026-12-27, or be left blank.")
assert_equal(check_input("PHL", "DEN", "2026-12-20", "2026-12-19"),
             "The return date cannot be before the departure date.")

# check_key
assert_equal(check_key(""), "Please enter your Ignav API key to continue.")
assert_equal(check_key("abc"), "That does not look like a whole API key. Paste it with no spaces.")
assert_equal(check_key("abcde fghij12"),
             "That does not look like a whole API key. Paste it with no spaces.")
assert_equal(check_key(TEST_KEY), "")

# is_checked
assert_equal(is_checked(True), True)
assert_equal(is_checked("True"), True)
assert_equal(is_checked(False), False)
assert_equal(is_checked(""), False)

# stay_place
assert_equal(stay_place("Breckenridge, CO", "DEN"), "Breckenridge, CO")
assert_equal(stay_place("", "DEN"), "DEN airport")

# make_example_stays and cheapest_stay
assert_equal(len(make_example_stays("Denver")), 4)
assert_equal(make_example_stays("Denver")[0], ExampleStay("Budget Inn near Denver", 79, 3.8))
assert_equal(cheapest_stay(make_example_stays("Denver")),
             ExampleStay("Hostel near Denver", 45, 4.0))

# encode_for_link
assert_equal(encode_for_link("Denver"), "Denver")
assert_equal(encode_for_link("Breckenridge, CO"), "Breckenridge%2C+CO")
assert_equal(encode_for_link("a/b&c"), "a%2Fb%26c")
assert_equal(encode_for_link("a\tb"), "a%09b")
assert_equal(encode_for_link("Zürich"), "Z%C3%BCrich")
assert_equal(encode_for_link(""), "")

# booking_link
assert_equal(booking_link("Denver", "2026-12-20", "2026-12-27"),
             "https://www.booking.com/searchresults.html?ss=Denver&checkin=2026-12-20"
             "&checkout=2026-12-27&group_adults=1&no_rooms=1&order=price")
assert_equal(booking_link("DEN airport", "2026-12-20", ""),
             "https://www.booking.com/searchresults.html?ss=DEN+airport"
             "&group_adults=1&no_rooms=1&order=price")

# clock_from_timestamp
assert_equal(clock_from_timestamp("2026-12-20T08:05:00"), "08:05")
assert_equal(clock_from_timestamp("08:05"), "08:05")
assert_equal(clock_from_timestamp(""), "")

# leg_airline
assert_equal(leg_airline({"carrier": "Delta"}), "Delta")
assert_equal(leg_airline({"segments": [{"operating_carrier_name": "United"}]}), "United")
assert_equal(leg_airline({}), "Unknown airline")

# leg_minutes
assert_equal(leg_minutes({"duration_minutes": 300}), 300)
assert_equal(leg_minutes({"segments": [{"duration_minutes": 60}, {"duration_minutes": 90}]}), 150)
assert_equal(leg_minutes({}), 0)

# flight_from_itinerary and parse_itineraries
SAMPLE_ITINERARY = {
    "price": {"amount": 215, "currency": "USD"},
    "outbound": {"carrier": "Delta", "duration_minutes": 300,
                 "segments": [{"departure_time_local": "2026-12-20T08:05:00",
                               "arrival_time_local": "2026-12-20T10:05:00"}]},
}
assert_equal(flight_from_itinerary(SAMPLE_ITINERARY), DELTA_FLIGHT)
assert_equal(parse_itineraries([SAMPLE_ITINERARY, {"price": {"amount": 99}, "outbound": {}}]),
             [DELTA_FLIGHT])
assert_equal(parse_itineraries([]), [])

# flight_price, flight_duration, stay_price
assert_equal(flight_price(DELTA_FLIGHT), 215.0)
assert_equal(flight_duration(DELTA_FLIGHT), 300)
assert_equal(stay_price(ExampleStay("Hostel near Denver", 45, 4.0)), 45)

# only_nonstop, sort_flights, find_cheapest_flight
assert_equal(only_nonstop([DELTA_FLIGHT, UNITED_FLIGHT]), [DELTA_FLIGHT])
assert_equal(only_nonstop([]), [])
assert_equal(sort_flights([DELTA_FLIGHT, UNITED_FLIGHT], "price"), [UNITED_FLIGHT, DELTA_FLIGHT])
assert_equal(sort_flights([UNITED_FLIGHT, DELTA_FLIGHT], "duration"),
             [DELTA_FLIGHT, UNITED_FLIGHT])
assert_equal(find_cheapest_flight([DELTA_FLIGHT, UNITED_FLIGHT]), UNITED_FLIGHT)
assert_equal(find_cheapest_flight([DELTA_FLIGHT]), DELTA_FLIGHT)

# format_money, describe_stops, describe_duration
assert_equal(format_money(215.4), "$215")
assert_equal(format_money(99.0), "$99")
assert_equal(describe_stops(0), "Nonstop")
assert_equal(describe_stops(1), "1 stop")
assert_equal(describe_stops(2), "2 stops")
assert_equal(describe_duration(330), "5h 30m")
assert_equal(describe_duration(45), "0h 45m")

# stay_nights, one_way_note, trip_type
ROUND_TRIP_STATE = State(TEST_KEY, "PHL", "DEN", "", "2026-12-20", "2026-12-23", False, "price",
                         [], "")
ONE_WAY_STATE = State(TEST_KEY, "PHL", "DEN", "", "2026-12-20", "", False, "price", [], "")
assert_equal(stay_nights(ROUND_TRIP_STATE), 3)
assert_equal(stay_nights(ONE_WAY_STATE), 1)
assert_equal(one_way_note(ROUND_TRIP_STATE), "")
assert_equal(one_way_note(ONE_WAY_STATE), " (one way: 1 night shown)")
assert_equal(trip_type(ROUND_TRIP_STATE), "round trip")
assert_equal(trip_type(ONE_WAY_STATE), "one way")

# Route: index with no key shows the key page
assert_equal(
    index(State("", "", "", "", "", "", False, "price", [], "")),
    Page(State("", "", "", "", "", "", False, "price", [], ""), [
        Header("Cheapest Trip Finder", 1),
        "Enter your Ignav API key to start. You only have to enter it once.",
        LineBreak(),
        "You can get a free key at ignav.com/signup.",
        LineBreak(),
        "Ignav API key:",
        TextBox("api_key", ""),
        LineBreak(),
        Button("Continue", save_key),
    ]))

# Route: index shows an error message once, then clears it
assert_equal(
    index(State("", "", "", "", "", "", False, "price", [],
                "Please enter your Ignav API key to continue.")),
    Page(State("", "", "", "", "", "", False, "price", [], ""), [
        Header("Cheapest Trip Finder", 1),
        "Enter your Ignav API key to start. You only have to enter it once.",
        LineBreak(),
        "You can get a free key at ignav.com/signup.",
        LineBreak(),
        "Please enter your Ignav API key to continue.",
        LineBreak(),
        "Ignav API key:",
        TextBox("api_key", ""),
        LineBreak(),
        Button("Continue", save_key),
    ]))

# Route: search_form with a saved key shows the form
assert_equal(
    search_form(State(TEST_KEY, "PHL", "DEN", "Breckenridge, CO", "2026-12-20", "2026-12-27",
                      True, "duration", [], "")),
    Page(State(TEST_KEY, "PHL", "DEN", "Breckenridge, CO", "2026-12-20", "2026-12-27",
               True, "duration", [], ""), [
        Header("Cheapest Trip Finder", 1),
        "Find the cheapest flight for your trip, then search for a place to stay.",
        LineBreak(),
        "Departing airport (like PHL):",
        TextBox("origin", "PHL"),
        LineBreak(),
        "Arrival airport (like DEN):",
        TextBox("destination", "DEN"),
        LineBreak(),
        "Where are you staying? (like Breckenridge, CO. Leave blank to stay near the "
        "arrival airport):",
        TextBox("stay_location", "Breckenridge, CO"),
        LineBreak(),
        "Departure date (YYYY-MM-DD):",
        TextBox("depart_date", "2026-12-20"),
        LineBreak(),
        "Return date (YYYY-MM-DD, leave blank for one way):",
        TextBox("return_date", "2026-12-27"),
        LineBreak(),
        CheckBox("nonstop_only", True),
        "Nonstop flights only",
        LineBreak(),
        "Sort flights by:",
        SelectBox("sort_by", ["price", "duration"], "duration"),
        LineBreak(),
        Button("Search", search_trip),
        Button("Change API key", forget_key),
    ]))

# Route: search_form with no key goes to the key page
assert_equal(search_form(State("", "", "", "", "", "", False, "price", [], "")),
             index(State("", "", "", "", "", "", False, "price", [], "")))

# Route: save_key with a good key saves it and opens the search form
assert_equal(save_key(State("", "", "", "", "", "", False, "price", [], ""),
                      "  " + TEST_KEY + " "),
             search_form(State(TEST_KEY, "", "", "", "", "", False, "price", [], "")))

# Route: save_key with a bad key stays on the key page and shows why
assert_equal(
    save_key(State("", "", "", "", "", "", False, "price", [], ""), "abc"),
    Page(State("", "", "", "", "", "", False, "price", [], ""), [
        Header("Cheapest Trip Finder", 1),
        "Enter your Ignav API key to start. You only have to enter it once.",
        LineBreak(),
        "You can get a free key at ignav.com/signup.",
        LineBreak(),
        "That does not look like a whole API key. Paste it with no spaces.",
        LineBreak(),
        "Ignav API key:",
        TextBox("api_key", ""),
        LineBreak(),
        Button("Continue", save_key),
    ]))

# Route: forget_key removes the key and an old error message
assert_equal(
    forget_key(State(TEST_KEY, "PHL", "DEN", "", "2026-12-20", "", False, "price", [],
                     "No flights were found for those choices. Try different ones.")),
    Page(State("", "PHL", "DEN", "", "2026-12-20", "", False, "price", [], ""), [
        Header("Cheapest Trip Finder", 1),
        "Enter your Ignav API key to start. You only have to enter it once.",
        LineBreak(),
        "You can get a free key at ignav.com/signup.",
        LineBreak(),
        "Ignav API key:",
        TextBox("api_key", ""),
        LineBreak(),
        Button("Continue", save_key),
    ]))

# Route: search_trip with a form problem shows the error page (no internet needed)
assert_equal(
    search_trip(State(TEST_KEY, "", "", "", "", "", False, "price", [], ""),
                " phl ", "phl", "", "2026-12-20", "", False, "price"),
    Page(State(TEST_KEY, "PHL", "PHL", "", "2026-12-20", "", False, "price", [],
               "Please choose two different airports."), [
        Header("Something went wrong", 1),
        "Please choose two different airports.",
        LineBreak(),
        Button("Try again", search_form),
    ]))

# Route: search_trip with no saved key goes to the key page
assert_equal(
    search_trip(State("", "", "", "", "", "", False, "price", [], ""),
                "PHL", "DEN", "", "2026-12-20", "", False, "price"),
    index(State("", "", "", "", "", "", False, "price", [], "")))

# Route: error_page
assert_equal(
    error_page(State(TEST_KEY, "", "", "", "", "", False, "price", [], "Oops.")),
    Page(State(TEST_KEY, "", "", "", "", "", False, "price", [], "Oops."), [
        Header("Something went wrong", 1),
        "Oops.",
        LineBreak(),
        Button("Try again", search_form),
    ]))

# Route: results with no flights
assert_equal(
    results(State(TEST_KEY, "", "", "", "", "", False, "price", [], "")),
    Page(State(TEST_KEY, "", "", "", "", "", False, "price", [], ""),
         ["There are no results yet.", Button("Start a search", search_form)]))

# Route: results for a round trip with one flight, staying in Breckenridge for 3 nights
assert_equal(
    results(State(TEST_KEY, "PHL", "DEN", "Breckenridge, CO", "2026-12-20", "2026-12-23",
                  False, "price", [DELTA_FLIGHT], "")),
    Page(State(TEST_KEY, "PHL", "DEN", "Breckenridge, CO", "2026-12-20", "2026-12-23",
               False, "price", [DELTA_FLIGHT], ""), [
        Header("PHL to DEN (round trip)", 1),
        "Live prices from Ignav.",
        LineBreak(),
        "Cheapest flight: $215 on Delta",
        LineBreak(),
        Header("Flights", 2),
        "$215 - Delta - Nonstop ",
        Button("Details", flight_details, [Argument("flight_number", 0)]),
        LineBreak(),
        Header("Places to stay near Breckenridge, CO", 2),
        "Hostel near Breckenridge, CO - $45/night - about $135 for 3 night(s) - rating 4.0",
        LineBreak(),
        "Budget Inn near Breckenridge, CO - $79/night - about $237 for 3 night(s) - rating 3.8",
        LineBreak(),
        "Hotel near Breckenridge, CO - $129/night - about $387 for 3 night(s) - rating 4.3",
        LineBreak(),
        "Resort near Breckenridge, CO - $219/night - about $657 for 3 night(s) - rating 4.6",
        LineBreak(),
        LineBreak(),
        Button("See trip total", trip_total),
        Button("New search", search_form),
    ]))

# Route: results for a one way trip shows the one way note
assert_equal(
    results(State(TEST_KEY, "PHL", "DEN", "", "2026-12-20", "", False, "price",
                  [DELTA_FLIGHT], "")),
    Page(State(TEST_KEY, "PHL", "DEN", "", "2026-12-20", "", False, "price",
               [DELTA_FLIGHT], ""), [
        Header("PHL to DEN (one way)", 1),
        "Live prices from Ignav.",
        LineBreak(),
        "Cheapest flight: $215 on Delta",
        LineBreak(),
        Header("Flights", 2),
        "$215 - Delta - Nonstop ",
        Button("Details", flight_details, [Argument("flight_number", 0)]),
        LineBreak(),
        Header("Places to stay near DEN airport", 2),
        "Hostel near DEN airport - $45/night - about $45 for 1 night(s) (one way: 1 night shown)"
        " - rating 4.0",
        LineBreak(),
        "Budget Inn near DEN airport - $79/night - about $79 for 1 night(s)"
        " (one way: 1 night shown) - rating 3.8",
        LineBreak(),
        "Hotel near DEN airport - $129/night - about $129 for 1 night(s) (one way: 1 night shown)"
        " - rating 4.3",
        LineBreak(),
        "Resort near DEN airport - $219/night - about $219 for 1 night(s) (one way: 1 night shown)"
        " - rating 4.6",
        LineBreak(),
        LineBreak(),
        Button("See trip total", trip_total),
        Button("New search", search_form),
    ]))

# Route: flight_details for the second flight in the list
assert_equal(
    flight_details(State(TEST_KEY, "PHL", "DEN", "", "2026-12-20", "2026-12-27", False, "price",
                         [DELTA_FLIGHT, UNITED_FLIGHT], ""), 1),
    Page(State(TEST_KEY, "PHL", "DEN", "", "2026-12-20", "2026-12-27", False, "price",
               [DELTA_FLIGHT, UNITED_FLIGHT], ""), [
        Header("Flight details", 1),
        Header("United", 2),
        "Price (round trip): $180 USD",
        LineBreak(),
        "Stops: 1 stop",
        LineBreak(),
        "Travel time: 7h 0m",
        LineBreak(),
        "Leaves PHL at 06:00 on 2026-12-20",
        LineBreak(),
        "Arrives in DEN at 13:00",
        LineBreak(),
        Button("Back to results", results),
    ]))

# Route: flight_details with a flight number that is not in the list
assert_equal(
    flight_details(State(TEST_KEY, "PHL", "DEN", "", "2026-12-20", "", False, "price",
                         [DELTA_FLIGHT], ""), 5),
    Page(State(TEST_KEY, "PHL", "DEN", "", "2026-12-20", "", False, "price",
               [DELTA_FLIGHT], "That flight could not be found."), [
        Header("Something went wrong", 1),
        "That flight could not be found.",
        LineBreak(),
        Button("Try again", search_form),
    ]))

# Route: trip_total with no flights
assert_equal(
    trip_total(State(TEST_KEY, "", "", "", "", "", False, "price", [], "")),
    Page(State(TEST_KEY, "", "", "", "", "", False, "price", [], ""),
         ["There are no results yet.", Button("Start a search", search_form)]))

# Route: trip_total for a one way trip, staying near the arrival airport
assert_equal(
    trip_total(State(TEST_KEY, "PHL", "DEN", "", "2026-12-20", "", False, "price",
                     [DELTA_FLIGHT, UNITED_FLIGHT], "")),
    Page(State(TEST_KEY, "PHL", "DEN", "", "2026-12-20", "", False, "price",
               [DELTA_FLIGHT, UNITED_FLIGHT], ""), [
        Header("Trip total", 1),
        "PHL to DEN (one way), staying near DEN airport",
        LineBreak(),
        "Cheapest flight: $180 on United",
        LineBreak(),
        "Cheapest place to stay: Hostel near DEN airport, $45/night x 1 night(s) = $45"
        " (one way: 1 night shown)",
        LineBreak(),
        Header("Total: $225", 2),
        "To see real hotel prices, copy this address into a new browser tab:",
        LineBreak(),
        "https://www.booking.com/searchresults.html?ss=DEN+airport&group_adults=1&no_rooms=1"
        "&order=price",
        LineBreak(),
        Button("Back to results", results),
        Button("New search", search_form),
    ]))

# Route: trip_total for a round trip, staying in Breckenridge for 3 nights
assert_equal(
    trip_total(State(TEST_KEY, "PHL", "DEN", "Breckenridge, CO", "2026-12-20", "2026-12-23",
                     False, "price", [DELTA_FLIGHT], "")),
    Page(State(TEST_KEY, "PHL", "DEN", "Breckenridge, CO", "2026-12-20", "2026-12-23",
               False, "price", [DELTA_FLIGHT], ""), [
        Header("Trip total", 1),
        "PHL to DEN (round trip), staying near Breckenridge, CO",
        LineBreak(),
        "Cheapest flight: $215 on Delta",
        LineBreak(),
        "Cheapest place to stay: Hostel near Breckenridge, CO, $45/night x 3 night(s) = $135",
        LineBreak(),
        Header("Total: $350", 2),
        "To see real hotel prices, copy this address into a new browser tab:",
        LineBreak(),
        "https://www.booking.com/searchresults.html?ss=Breckenridge%2C+CO&checkin=2026-12-20"
        "&checkout=2026-12-23&group_adults=1&no_rooms=1&order=price",
        LineBreak(),
        Button("Back to results", results),
        Button("New search", search_form),
    ]))


start_server(State("", "", "", "", "", "", False, "price", [], ""))
