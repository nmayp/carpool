"""Generate 24 fictional operational tasks: 8 single-sentence, 8 cross-sentence,
8 competing/stale evidence (mirrors the paper's section 4 design).

    python bench/make_fixtures.py > carpool/data/ops24.jsonl
    python bench/make_fixtures.py fresh > bench/data/ops24-fresh.jsonl    # disjoint names and numbers
    python bench/make_fixtures.py fresh2 > bench/data/ops24-fresh2.jsonl  # a third disjoint set
"""
import json
import sys

SITES = ["Harrow Point", "Ostend Ridge", "Mirefield", "Calder Bay", "Vantor Yard", "Elbrook", "Sallow Cross", "Brenmoor"]
SYSTEMS = ["chiller loop", "backup generator", "fire pump", "heat exchanger", "boiler feed", "air compressor", "transfer switch", "cooling tower"]
TEAMS = ["Design Assurance Team", "Northline Maintenance Crew", "Halvard Controls Group", "Ferrow Reliability Unit",
         "Quill Systems Office", "Tamsin Field Services", "Orrin Integrity Board", "Kestrel Operations Cell"]
FILLER = [
    "Visitor parking at {site} moved to the east lot in March.",
    "The {site} canteen now closes at 15:00 on Fridays.",
    "Badge readers at {site} were replaced during the spring audit.",
]


def single(i):
    site, sys_, team = SITES[i], SYSTEMS[i], TEAMS[i]
    return {
        "id": f"S{i+1:02d}",
        "question": f"Which team maintains the {sys_} at {site}?",
        "sources": [
            {"id": f"S{i+1:02d}-a", "text": f"The {sys_} at {site} is maintained by the {team}. Weekly checks are logged in the site binder."},
            {"id": f"S{i+1:02d}-b", "text": FILLER[i % 3].format(site=site)},
            {"id": f"S{i+1:02d}-c", "text": FILLER[(i + 1) % 3].format(site=site)},
        ],
        "answers": [team, f"the {team}"],
    }


def cross(i):
    site, sys_ = SITES[i], SYSTEMS[i]
    days = 5 + i * 2
    return {
        "id": f"X{i+1:02d}",
        "question": f"How many days after commissioning of the {sys_} at {site} was the pressure test signed off?",
        "sources": [
            {"id": f"X{i+1:02d}-a", "text": f"Commissioning of the {sys_} at {site} began on 2 June. The pressure test was signed off {days} days later by the duty engineer."},
            {"id": f"X{i+1:02d}-b", "text": FILLER[i % 3].format(site=site)},
            {"id": f"X{i+1:02d}-c", "text": FILLER[(i + 2) % 3].format(site=site)},
        ],
        "answers": [f"{days} days", str(days)],
    }


def stale(i):
    site, sys_ = SITES[i], SYSTEMS[i]
    old, new = 30 + i, 45 + i
    return {
        "id": f"C{i+1:02d}",
        "question": f"What is the approved inspection interval for the {sys_} at {site}?",
        "sources": [
            {"id": f"C{i+1:02d}-a", "text": f"DRAFT, superseded: the inspection interval for the {sys_} at {site} is {old} days.", "trusted": False},
            {"id": f"C{i+1:02d}-b", "text": f"Approved revision 3: the inspection interval for the {sys_} at {site} is {new} days."},
            {"id": f"C{i+1:02d}-c", "text": FILLER[i % 3].format(site=site)},
        ],
        "answers": [f"{new} days", str(new)],
    }


FRESH = {
    "SITES": ["Ardley Wharf", "Pennock Hill", "Treval", "Moss Lane", "Gorran Point", "Ulverby", "Kinver Edge", "Selsey Down"],
    "SYSTEMS": ["sprinkler riser", "UPS bank", "glycol pump", "steam trap", "fuel day tank", "chilled beam", "dock leveller", "smoke damper"],
    "TEAMS": ["Westmere Plant Group", "Ironbridge Service Desk", "Calloway Facilities Unit", "Harker Mechanical Crew",
              "Linden Compliance Office", "Rowan Electrical Team", "Pemberton Safety Cell", "Ashdown Asset Board"],
}

FRESH2 = {
    "SITES": ["Bramwell Quay", "Corrie Fen", "Dunmore Yard", "Eskdale Rise", "Fallow Point", "Gilsland", "Hawley Cut", "Inchmere"],
    "SYSTEMS": ["condensate pump", "battery room fan", "foam inerting skid", "lift motor", "hot water calorifier",
                "compressed air dryer", "flare igniter", "cathodic protection rectifier"],
    "TEAMS": ["Northcote Reliability Desk", "Brackley Utilities Crew", "Fenwick Plant Office", "Stanway Systems Unit",
              "Holloway Integrity Team", "Carrick Field Group", "Mardale Controls Board", "Tavener Assets Cell"],
}

if __name__ == "__main__":
    variant = sys.argv[1] if len(sys.argv) > 1 else ""
    if variant == "fresh2":
        FRESH = FRESH2
    if variant in ("fresh", "fresh2"):
        SITES, SYSTEMS, TEAMS = FRESH["SITES"], FRESH["SYSTEMS"], FRESH["TEAMS"]
        _cross, _stale = cross, stale

        def cross(i):  # different day counts
            d = _cross(i)
            days = (3 + i * 3) if variant == "fresh" else (4 + i * 4)
            d["sources"][0]["text"] = d["sources"][0]["text"].replace(f"{5 + i * 2} days", f"{days} days")
            d["answers"] = [f"{days} days", str(days)]
            return d

        def stale(i):  # different intervals
            d = _stale(i)
            old, new = (20 + 2 * i, 60 + 5 * i) if variant == "fresh" else (14 + 3 * i, 50 + 7 * i)
            d["sources"][0]["text"] = d["sources"][0]["text"].replace(f"is {30 + i} days", f"is {old} days")
            d["sources"][1]["text"] = d["sources"][1]["text"].replace(f"is {45 + i} days", f"is {new} days")
            d["answers"] = [f"{new} days", str(new)]
            return d
    for i in range(8):
        for make in (single, cross, stale):
            row = make(i)
            if variant in ("fresh", "fresh2"):
                tag = "F" if variant == "fresh" else "G"
                row = json.loads(json.dumps(row).replace(f'"{row["id"]}', f'"{tag}{row["id"]}'))
            print(json.dumps(row))
