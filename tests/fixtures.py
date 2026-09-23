"""Payload finti ma strutturalmente identici a quelli reali dell'API,
usati dal test di parsing (nessuna chiamata di rete)."""

SAMPLE_REGULAR_WAR = {
    "state": "warEnded",
    "teamSize": 2,
    "preparationStartTime": "20260920T180000.000Z",
    "startTime": "20260921T180000.000Z",
    "endTime": "20260922T180000.000Z",
    "result": "win",
    "clan": {
        "tag": "#OWNCLAN",
        "name": "Il Mio Clan",
        "stars": 5,
        "destructionPercentage": 91.5,
        "members": [
            {
                "tag": "#P1",
                "name": "Mario",
                "townhallLevel": 15,
                "mapPosition": 1,
                "attacks": [
                    {"order": 1, "attackerTag": "#P1", "defenderTag": "#O1",
                     "stars": 3, "destructionPercentage": 100, "duration": 120},
                ],
                "opponentAttacks": 1,
            },
            {
                "tag": "#P2",
                "name": "Luigi",
                "townhallLevel": 14,
                "mapPosition": 2,
                "attacks": [
                    {"order": 3, "attackerTag": "#P2", "defenderTag": "#O2",
                     "stars": 2, "destructionPercentage": 85, "duration": 130},
                ],
                "opponentAttacks": 1,
            },
        ],
    },
    "opponent": {
        "tag": "#OPPCLAN",
        "name": "Clan Rivale",
        "stars": 5,
        "destructionPercentage": 90.0,
        "members": [
            {
                "tag": "#O1",
                "name": "Bowser",
                "townhallLevel": 15,
                "mapPosition": 1,
                "attacks": [
                    {"order": 2, "attackerTag": "#O1", "defenderTag": "#P1",
                     "stars": 2, "destructionPercentage": 80, "duration": 140},
                ],
                "opponentAttacks": 1,
            },
            {
                "tag": "#O2",
                "name": "Peach",
                "townhallLevel": 14,
                "mapPosition": 2,
                "attacks": [
                    {"order": 4, "attackerTag": "#O2", "defenderTag": "#P2",
                     "stars": 3, "destructionPercentage": 100, "duration": 150},
                ],
                "opponentAttacks": 1,
            },
        ],
    },
}
