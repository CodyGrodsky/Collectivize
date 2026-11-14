"""Fictional content for the bulk mock data: campuses, org templates,
event templates and names.

Everything here is invented. Campus and org names get the "[Demo]"
prefix when seeded, and people the "(demo ...)" suffix, so none of it can
be mistaken for a real record.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EventTemplate:
    title: str
    description: str
    hours: int = 2
    capacity: int | None = None
    members_only: bool = False


@dataclass(frozen=True)
class OrgTemplate:
    name: str
    tags: tuple[str, ...]
    events: tuple[EventTemplate, ...]
    meeting: str | None = None  # title of a weekly series, if the org runs one
    sports: bool = False  # sits under the campus Club Sports Council


@dataclass(frozen=True)
class CampusTemplate:
    key: str
    name: str
    offset_km: tuple[float, float]  # (north, east) of the main demo campus
    buildings: tuple[str, ...]


EXTRA_TAGS = {
    "music": "Music",
    "arts": "Arts",
    "theater": "Theater",
    "dance": "Dance",
    "cultural": "Cultural",
    "service": "Community service",
    "outdoors": "Outdoors",
    "tech": "Tech",
    "career": "Career",
    "gaming": "Gaming",
    "wellness": "Wellness",
    "food": "Food",
    "debate": "Debate",
    "environment": "Environment",
    "entrepreneurship": "Entrepreneurship",
    "media": "Media",
    "faith": "Faith",
    "greek-life": "Greek life",
}

# The first campus reuses the hand-built "Northbridge University" demo org.
CAMPUSES = (
    CampusTemplate("northbridge", "Northbridge University", (0.0, 0.0), (
        "Student Center", "Engineering Hall", "Library", "Arts Annex", "Rec Center",
        "Hale Auditorium", "Quad Lawn", "Science Tower", "Dining Commons",
    )),
    CampusTemplate("riverside", "Riverside College", (-3.2, 4.1), (
        "Riverside Union", "Boathouse", "Marsh Hall", "Fine Arts Center",
        "Riverside Gym", "Old Chapel", "Greenhouse", "Lecture Hall B",
    )),
    CampusTemplate("harborview", "Harborview Institute", (4.4, -2.7), (
        "Harbor Commons", "Innovation Lab", "Pier Pavilion", "Maker Garage",
        "West Library", "Harborview Arena", "Media Studio", "Courtyard",
    )),
)

GREEK_NATIONALS = (
    "Kappa Lambda", "Theta Rho", "Omicron Pi Nu", "Zeta Chi", "Lambda Sigma Upsilon",
    "Phi Omega", "Delta Iota", "Mu Epsilon", "Xi Tau", "Rho Kappa Psi",
)
CHAPTER_NAMES = (
    "Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta",
    "Iota", "Kappa", "Lambda", "Mu",
)
CHAPTER_EVENTS = (
    EventTemplate("Philanthropy car wash",
                  "Cars washed by hand all afternoon; every dollar goes to the local "
                  "children's hospital.", hours=4),
    EventTemplate("Open recruitment night",
                  "Meet the brothers and sisters, hear about the chapter and ask "
                  "anything. Snacks provided.", capacity=80),
    EventTemplate("Chapter formal",
                  "The semester's formal dinner and dance.", hours=4, members_only=True),
    EventTemplate("Pancake breakfast fundraiser",
                  "All-you-can-eat pancakes to raise money for the food pantry.", hours=3),
    EventTemplate("Alumni networking mixer",
                  "Current members meet chapter alumni working in the area.",
                  members_only=True),
    EventTemplate("Neighbourhood clean-up",
                  "Gloves and bags provided. We walk the blocks around campus and "
                  "pick up litter.", hours=3),
)

CLUBS = (
    OrgTemplate("A Cappella Collective", ("music", "arts"), (
        EventTemplate("Fall a cappella showcase",
                      "Five groups, one stage, an hour of harmonies.", capacity=200),
        EventTemplate("Open auditions",
                      "Sing sixteen bars of anything. No experience needed."),
    ), meeting="Rehearsal"),
    OrgTemplate("Film Society", ("arts", "media"), (
        EventTemplate("Midnight cult classic screening",
                      "Popcorn, costumes encouraged, audience participation required."),
        EventTemplate("Student short film night",
                      "Members screen their own shorts and take questions."),
    ), meeting="Weekly screening"),
    OrgTemplate("Hackers Guild", ("tech", "academic"), (
        EventTemplate("24-hour hackathon",
                      "Build anything in a day. Food, prizes and mentors on site.",
                      hours=24, capacity=150),
        EventTemplate("Intro to Git workshop",
                      "Branches, commits and pull requests from scratch.", capacity=40),
        EventTemplate("Tech talk: building search engines",
                      "A visiting engineer explains how ranking works."),
    ), meeting="Hack night"),
    OrgTemplate("Outing Club", ("outdoors", "wellness"), (
        EventTemplate("Sunrise hike",
                      "Meet at dawn, hike to the overlook, breakfast at the top.",
                      hours=4, capacity=25),
        EventTemplate("Gear swap",
                      "Bring outdoor gear you no longer use; take something you need."),
        EventTemplate("Intro to rock climbing",
                      "Harnesses and shoes provided. Beginners welcome.", capacity=20),
    )),
    OrgTemplate("Debate Union", ("debate", "academic"), (
        EventTemplate("Public debate: should cities ban cars downtown?",
                      "Two teams, one motion, and the audience votes."),
        EventTemplate("Novice tournament",
                      "A friendly first tournament for new debaters.", hours=6),
    ), meeting="Practice round"),
    OrgTemplate("Environmental Action", ("environment", "service"), (
        EventTemplate("Campus tree planting",
                      "We plant forty saplings along the river path.", hours=3),
        EventTemplate("Repair cafe",
                      "Bring broken lamps, clothes and bikes; volunteers help fix them."),
    )),
    OrgTemplate("Entrepreneurs Network", ("entrepreneurship", "career"), (
        EventTemplate("Startup pitch night",
                      "Six student teams pitch to a panel of local founders.",
                      capacity=120),
        EventTemplate("Founder fireside chat",
                      "An informal Q&A with a founder who started in a dorm room."),
    )),
    OrgTemplate("Board Game Guild", ("gaming", "social"), (
        EventTemplate("Board game marathon",
                      "Over a hundred games, from quick party games to six-hour epics.",
                      hours=8),
        EventTemplate("Tabletop RPG one-shots",
                      "Drop-in role-playing sessions run by experienced game masters."),
    ), meeting="Game night"),
    OrgTemplate("Esports Club", ("gaming", "tech"), (
        EventTemplate("Fighting game tournament",
                      "Double elimination bracket. Bring your own controller.",
                      hours=5, capacity=64),
        EventTemplate("Watch party: championship finals",
                      "The finals on the big screen with snacks."),
    )),
    OrgTemplate("Culinary Society", ("food", "social"), (
        EventTemplate("Dumpling folding workshop",
                      "Learn three folds, then eat what you made.", capacity=30),
        EventTemplate("Bake-off",
                      "Bring a bake, taste everyone else's, judges pick a winner."),
    )),
    OrgTemplate("Coffee Lovers Club", ("food", "social"), (
        EventTemplate("Pour-over tasting",
                      "Four single-origin coffees side by side.", capacity=24),
    )),
    OrgTemplate("Dance Collective", ("dance", "arts"), (
        EventTemplate("Spring dance concert",
                      "Hip hop, contemporary and ballroom pieces by student choreographers.",
                      capacity=250),
        EventTemplate("Salsa social",
                      "A quick lesson, then open dancing. No partner needed."),
    ), meeting="Open practice"),
    OrgTemplate("Theater Players", ("theater", "arts"), (
        EventTemplate("Opening night: a new student play",
                      "The world premiere of this year's student-written play.",
                      hours=3, capacity=180),
        EventTemplate("Improv jam",
                      "Short-form improv games. Watch or jump in."),
    )),
    OrgTemplate("Jazz Ensemble", ("music",), (
        EventTemplate("Jazz on the lawn",
                      "Standards and originals outdoors. Bring a blanket."),
    ), meeting="Ensemble rehearsal"),
    OrgTemplate("Photography Club", ("arts", "media"), (
        EventTemplate("Golden hour photo walk",
                      "A walk through the old town at sunset with tips along the way."),
        EventTemplate("Darkroom basics",
                      "Develop your first roll of film.", capacity=12),
    )),
    OrgTemplate("Student Radio", ("media", "music"), (
        EventTemplate("Radio open house",
                      "Tour the studio and record a ten-second station ID."),
        EventTemplate("Battle of the bands",
                      "Six student bands, three judges, one live broadcast.", hours=4),
    )),
    OrgTemplate("Volunteer Corps", ("service", "philanthropy"), (
        EventTemplate("Food bank sorting shift",
                      "Sort donations for the weekend distribution.", hours=3, capacity=30),
        EventTemplate("Reading buddies",
                      "Read with local elementary school students.", capacity=20),
    )),
    OrgTemplate("Mindfulness Circle", ("wellness",), (
        EventTemplate("Guided meditation",
                      "Thirty minutes of guided breathing for stressed students.", hours=1),
        EventTemplate("Yoga on the quad",
                      "Gentle all-levels yoga. Mats provided.", hours=1),
    ), meeting="Weekly sit"),
    OrgTemplate("Interfaith Council", ("faith", "cultural"), (
        EventTemplate("Interfaith dinner",
                      "Students of many traditions share a meal and conversation."),
    )),
    OrgTemplate("International Students Association", ("cultural", "social"), (
        EventTemplate("Global food festival",
                      "Dishes from thirty countries cooked by students.", hours=3),
        EventTemplate("Language exchange cafe",
                      "Practise a language you're learning with a native speaker."),
    )),
    OrgTemplate("Pre-Med Society", ("academic", "career"), (
        EventTemplate("MCAT strategy panel",
                      "Students who scored well share what worked."),
        EventTemplate("Hospital volunteering info session",
                      "How to find a volunteering placement."),
    )),
    OrgTemplate("Women in Engineering", ("tech", "career"), (
        EventTemplate("Resume review night",
                      "Engineers from local firms review your resume one-on-one."),
        EventTemplate("Build-a-circuit workshop",
                      "Solder your first blinking LED badge.", capacity=30),
    )),
    OrgTemplate("Astronomy Club", ("academic", "outdoors"), (
        EventTemplate("Star party",
                      "Telescopes on the roof. Saturn should be visible.", hours=3),
    )),
    OrgTemplate("Chess and Go Society", ("gaming", "academic"), (
        EventTemplate("Blitz tournament",
                      "Five-minute games, Swiss pairing, all ratings.", hours=4),
    ), meeting="Club night"),
    OrgTemplate("Ultimate Frisbee", ("intramural-sports", "outdoors"), (
        EventTemplate("Hat tournament",
                      "Random teams drawn from a hat. All skill levels.", hours=5),
    ), meeting="Pickup practice", sports=True),
    OrgTemplate("Club Volleyball", ("intramural-sports",), (
        EventTemplate("Home tournament",
                      "We host four teams from around the region.", hours=6),
        EventTemplate("Tryouts", "Open tryouts for the travel team."),
    ), sports=True),
    OrgTemplate("Running Club", ("intramural-sports", "wellness"), (
        EventTemplate("River loop 10K",
                      "A social 10K along the river. Pace groups for everyone.", hours=2),
    ), meeting="Group run", sports=True),
    OrgTemplate("Rowing Club", ("intramural-sports", "outdoors"), (
        EventTemplate("Learn to row day",
                      "Try a rowing machine, then a real boat.", capacity=24),
    ), sports=True),
    OrgTemplate("Climbing Team", ("intramural-sports", "outdoors"), (
        EventTemplate("Bouldering competition",
                      "Thirty new problems, scored on tops and attempts.", hours=4),
    ), sports=True),
    OrgTemplate("Fencing Club", ("intramural-sports",), (
        EventTemplate("Try fencing night",
                      "Masks and foils provided; learn the basics in an hour."),
    ), sports=True),
)

FIRST_NAMES = (
    "Abe", "Addison", "Ainsley", "Alex", "Amani", "Arden", "Ash", "Aspen", "August",
    "Bailey", "Beck", "Bellamy", "Bryn", "Cade", "Camden", "Carmen", "Cass", "Charlie",
    "Cyan", "Dakota", "Dana", "Darcy", "Devon", "Dylan", "Eden", "Ellis", "Emerson",
    "Ezra", "Frankie", "Gabi", "Greer", "Hayden", "Hunter", "Imani", "Jaden", "Jamie",
    "Jesse", "Joss", "Kamari", "Keegan", "Kendall", "Kiran", "Lark", "Leighton", "Lennon",
    "Linden", "Luca", "Mackenzie", "Maddox", "Mika", "Milan", "Monroe", "Nadia", "Nell",
    "Noa", "Oli", "Palmer", "Phoenix", "Presley", "Raine", "Reese", "Remy", "River",
    "Robin", "Rowan", "Rory", "Sam", "Sasha", "Sawyer", "Shay", "Sky", "Spencer",
    "Stevie", "Sutton", "Tatum", "Teagan", "Toby", "Tristan", "Vesper", "Winter",
)
LAST_INITIALS = "ABCDEFGHJKLMNOPRSTVWZ"

COUNCIL_EVENTS = (
    EventTemplate("General assembly",
                  "Every member org sends a delegate to vote on this term's budget."),
    EventTemplate("Leadership training",
                  "Officer training on running meetings, budgets and events.", hours=3),
    EventTemplate("Involvement fair",
                  "Every member org has a table. Come find your people.", hours=4),
)
NATIONAL_EVENTS = (
    EventTemplate("Regional leadership summit",
                  "Chapter officers from across the region meet for workshops.", hours=6),
    EventTemplate("Founders' day celebration",
                  "Members and alumni from every chapter celebrate the founding."),
)
STUDENT_EVENTS = (
    EventTemplate("Study group: organic chemistry",
                  "Working through the practice exam together before the midterm."),
    EventTemplate("Pickup basketball",
                  "Casual games, teams picked on the spot."),
    EventTemplate("Picnic in the park",
                  "Bring a snack to share. Frisbees welcome.", hours=3),
    EventTemplate("Thrift store crawl",
                  "Four thrift shops in one afternoon.", hours=3),
    EventTemplate("Sketching meetup",
                  "Draw the architecture around campus. All levels.", capacity=15),
)
