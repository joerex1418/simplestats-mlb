"""# SimpleStatsMLB

Python Wrapper for the MLB Stats API - https://statsapi.mlb.com

- Author: Joe Rechenmacher
- GitHub Profile: https://github.com/joerex1418

### Copyright Notice
This repository and its author are not affiliated with the MLB in any way. 
Use of MLB data is subject to the notice posted at 
http://gdx.mlb.com/components/copyright.txt.

Overview
--------
SimpleStatsMLB is designed to make retrieving and visualizing MLB data as easy 
& intuitive as possible.
    
With a variety of data tables, 4 primary classes, and almost 40 functions, 
getting the baseball information you need should be a "can-o-corn".

-------------------------------------------------------------------------------
Highlights
----------
### 4 Primary Classes designed to quickly make hundreds of API calls at once
    * `Person` - instances represent a specific person affiliated with the MLB
        * Primary usage is for the retrieval of player bios and stats
    * `Franchise` - instances represent an MLB franchise (i.e. the parent org 
    for specific team)
    * `Team` - instances represent a specific team for a specified season
    * `Game` - instances represent LIVE data for a game
        * Available data includes scores, game stats, lineups, pitch logs, play
        logs, weather, attendance, venue info, and more!

### Pitch & Play Finder!
    * With the `pitch_search` and `play_search` functions, you'll be able to 
    search for any plays/pitches over recent years with detailed information 

### Schedule
    * Need the entire 2021 schedule? Or just the 2005 schedule for the Chicago 
    White Sox?
### Just need to know when your team is playing next? 
    Getting the dates that you need is easy with the `schedule` function
    
    * Use the `next_game` function to get the 'when', 'who', and 'where' for 
    your favorite team's next matchup


### Find out more by clicking the links below!
-------------------------------------------------------------------------------
- Source: https://github.com/joerex1418/simplestats-mlb
- Issues: https://github.com/joerex1418/simplestats-mlb/issues
- License: https://raw.githubusercontent.com/joerex1418/simplestats-mlb/master/LICENSE

"""

from .classes import Person
from .classes import Franchise
from .classes import Team
from .game import Game
from .classes import api
franchise = Franchise
person = Person
team = Team
game = Game


from .functions import play_search
from .functions import pitch_search
from .functions import game_search
# from .functions import get_matchup_stats
from .functions import last_game
from .functions import next_game
from .functions import find_team
from .functions import find_venue
from .functions import schedule
from .functions import scores
from .functions import games_today
from .functions import free_agents
from .functions import player_bio
from .functions import player_stats
from .functions import player_game_logs
from .functions import player_date_range
from .functions import player_date_range_advanced
from .functions import player_splits
from .functions import player_splits_advanced
from .functions import team_roster
from .functions import team_game_logs
from .functions import team_appearances
from .functions import team_stats
from .functions import league_stats
from .functions import league_leaders
from .functions import season_standings
from .functions import game_highlights
from .functions import get_video_link
from .functions import league

from .utils import keys
from .utils import timeutils
from .utils import default_season
from .utils import metadata
from .utils import COLS_HIT

from .mlbdata import get_teams_df as teams
from .mlbdata import get_people_df as people
from .mlbdata import get_venues_df as venues
from .mlbdata import get_leagues_df as leagues
from .mlbdata import get_seasons_df as seasons
from .mlbdata import get_standings_df as standings
from .mlbdata import get_yby_records as yby_records
from .mlbdata import get_hall_of_fame as hall_of_fame
from .mlbdata import get_broadcasts_df as broadcasts
from .mlbdata import get_pitch_types_df as pitch_types
from .mlbdata import get_pitch_codes_df as pitch_codes
from .mlbdata import get_bbref_data as bbref_data
from .mlbdata import get_event_types_df as event_types
from .mlbdata import get_bbref_hitting_war_df as bbref_war_hit
from .mlbdata import get_bbref_pitching_war_df as bbref_war_pitch
from .mlbdata import get_teams_from_register_df as chadwick_teams
from .mlbdata import get_coaches as coaches
legends = hall_of_fame

from .updatedb import update_hof
from .updatedb import update_people
from .updatedb import update_venues
from .updatedb import update_seasons
from .updatedb import update_leagues
from .updatedb import update_yby_records
from .updatedb import update_bbref_data
from .updatedb import update_bbref_hitting_war
from .updatedb import update_bbref_pitching_war
from .updatedb import update_pitch_types
from .updatedb import update_pitch_codes
from .updatedb import update_event_types
from .updatedb import update_coaches
from .updatedb import update_standings
update_legends = update_hof
update_bbref_batting_war = update_bbref_hitting_war

from .async_mlb import fetch
from .async_mlb import fetch_text

from .paths import *

from . import constants
from .mlb_dataclasses import Leagues

from .objects import MlbWrapper
from .objects import MlbDate
from .objects import MlbDatetime
from .objects import league_ref
    
    
    
