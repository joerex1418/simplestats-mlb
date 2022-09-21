import datetime as dt
from collections import namedtuple
from typing import Union, Optional, Literal, NamedTuple

import pytz
import pandas as pd
from dateutil.parser import parse

from tabulate import tabulate as tab

from . import mlbdata
from . import mlb_dataclasses as dclass

TEAMS = mlbdata.get_teams_df()
LEAGUES = mlbdata.get_leagues_df()

League = namedtuple('League',['full','short','abbreviation','child_division','parent_league'])

class LeagueData:
    def __init__(self,row: pd.Series):
        self.__id = row['mlbam']
        self.__full = row['name_full']
        self.__short = row['name_short']
        self.__division = row['div_part']
        self.__abbrv = row['abbreviation']
        self.__parent_mlbam = row['parent_mlbam']
        
    def __repr__(self):
        return f'<mlb.LeagueEntry - {self.__id} | {self.__short}>'
    
    @property
    def full(self):
        return self.__full
    
    @property
    def short(self):
        return self.__short
    
    @property
    def division(self):
        return self.__division
    
    @property
    def abbreviation(self):
        return self.__abbrv
    
    @property
    def parent_mlbam(self):
        return self.__parent_mlbam
    
    parent_id = parent_mlbam

league_ref: dict[Union[int,str],LeagueData] = {}
for idx,row in LEAGUES.iterrows():
    mlbam = row['mlbam']
    
    league_ref[str(mlbam)] = LeagueData(row)
    league_ref[int(mlbam)] = league_ref[str(mlbam)]

class StandingsWrapper:
    def __new__(cls, records, splits=None):
        self = object.__new__(cls)
        self.__records = records
        self.__splits = splits

        return self

    @property
    def records(self) -> pd.DataFrame:
        """Year-by-year season records"""
        return self.__records

    @property
    def splits(self) -> pd.DataFrame:
        """Year-by-year season record splits"""
        return self.__splits


class RosterWrapper:
    def __new__(cls, **rosters):
        self = object.__new__(cls)
        # self.__dict__.update(rosters)
        self.__all = rosters["all"]
        self.__pitcher = rosters["pitcher"]
        self.__catcher = rosters["catcher"]
        self.__first = rosters["first"]
        self.__second = rosters["second"]
        self.__third = rosters["third"]
        self.__short = rosters["short"]
        self.__left = rosters["left"]
        self.__center = rosters["center"]
        self.__right = rosters["right"]
        self.__dh = rosters["dh"]
        self.__infield = rosters["infield"]
        self.__outfield = rosters["outfield"]
        self.__active = rosters["active"]

        return self

    def __get__(self):
        return self.all

    def __getitem__(self, __attr) -> pd.DataFrame:
        return getattr(self, __attr)

    def __len__(self) -> int:
        return len(self.all)

    def __call__(self, _pos=None) -> pd.DataFrame:
        df = self.all
        if _pos is not None:
            df = df[df["pos"] == _pos]
        return df

    @property
    def all(self) -> pd.DataFrame:
        """Full Roster

        - Shows alltime players for for Franchise object
        - Shows all players in a given season for Team object
        """
        return self.__all

    @property
    def pitcher(self) -> pd.DataFrame:
        """All Pitchers"""
        return self.__pitcher

    @property
    def catcher(self) -> pd.DataFrame:
        """All Catchers"""
        return self.__catcher

    @property
    def first(self) -> pd.DataFrame:
        """All First Basemen"""
        return self.__first

    @property
    def second(self) -> pd.DataFrame:
        """All Second Basemen"""
        return self.__second

    @property
    def third(self) -> pd.DataFrame:
        """All Third Basemen"""
        return self.__third

    @property
    def short(self) -> pd.DataFrame:
        """All Shortstops"""
        return self.__short

    @property
    def left(self) -> pd.DataFrame:
        """All Left Fielders"""
        return self.__left

    @property
    def center(self) -> pd.DataFrame:
        """All Center Fielders"""
        return self.__center

    @property
    def right(self) -> pd.DataFrame:
        """All Right Fielders"""
        return self.__right

    @property
    def infield(self) -> pd.DataFrame:
        """All Infielders"""
        return self.__infield

    @property
    def outfield(self) -> pd.DataFrame:
        """All Outfielders"""
        return self.__outfield

    @property
    def dh(self) -> pd.DataFrame:
        """All Outfielders"""
        return self.__dh

    @property
    def designated_hitter(self) -> pd.DataFrame:
        """All Outfielders"""
        return self.__dh

    @property
    def active(self) -> pd.DataFrame:
        """All active players"""
        return self.__active


class MlbDate:
    """datetime.date wrapper for mlb date representations

    Properties
    ----------
    - 'month'
    - 'day'
    - 'year'
    - 'day_of_week' | 'dow'
    - 'day_of_week_short' | 'dow_short'
    - 'month_name'
    - 'month_name_short'
    - 'obj'
    - 'string'
    - 'full'
    - 'short'
    """

    def __init__(self, _date_string: str,tz=None):
        if _date_string != "-":
            self.dt_obj = parse(_date_string)
            self.__tz = tz
            if tz is not None:
                self.dt_obj = self.dt_obj.astimezone(tz=tz)
            d = self.dt_obj.date()
            self.__date_obj = d
            self.__date_str = self.__date_obj.strftime(r"%Y-%m-%d")
            self.__month_name = d.strftime(r"%B")
            self.__month_name_short = d.strftime(r"%b")
            self.__dow = d.strftime(r"%A")
            self.__dow_short = d.strftime(r"%a")
            self.__day = str(int(d.strftime(r"%d")))
            self.__month = d.strftime(r"%m")
            self.__year = d.strftime(r"%Y")
            self.__full_date = f"{self.__month_name} {self.__day}, {self.__year}"
            self.__short_date = f"{self.__month_name_short} {self.__day}, {self.__year}"

        else:
            self.__date_obj = None
            self.__date_str = "-"
            self.__month_name = "-"
            self.__month_name_short = "-"
            self.__dow = "-"
            self.__dow_short = "-"
            self.__month = 0
            self.__day = 0
            self.__year = 0
            self.__full_date = "-"
            self.__short_date = "-"

    def __repr__(self):
        return self.__date_str

    def __str__(self):
        return self.__date_str

    def __call__(self,fmt=None) -> Union[dt.date,str]:
        """Returns `date` object from built-in datetime module.
        Alternatively, a string format can be specified instead
        
        Paramaters:
        -----------
        fmt : str
            Date string format to return
        """
        dt_obj = self.__date_obj
        if fmt is None:
            return dt_obj
        else:
            return dt_obj.strftime(fmt)
        
    @property
    def month(self) -> int:
        """Returns month as integer (1-12)"""
        return self.__month

    @property
    def day(self) -> int:
        """Returns day as integer (1-31)"""
        return self.__day

    @property
    def year(self) -> int:
        """Returns year as integer"""
        return self.__year

    @property
    def day_of_week(self) -> str:
        """Returns day of week as string (Example: "Wednesday")"""
        return self.__dow

    @property
    def dow(self) -> str:
        """Returns day of week as string (Example: "Wednesday")

        ALIAS for 'day_of_week'
        """
        return self.__dow

    @property
    def day_of_week_short(self) -> str:
        """Returns shortened name of day of week as string (Example: "Wed")"""
        return self.__dow_short

    @property
    def dow_short(self) -> str:
        """Returns shortened name of day of week as string (Example: "Wed")"""
        return self.__dow_short

    @property
    def month_name(self) -> str:
        """Returns full month name as string (Example: "February")"""
        return self.__month_name

    @property
    def month_name_short(self) -> str:
        """Returns shortened month name as string (Example: "Feb")"""
        return self.__month_name_short

    @property
    def obj(self) -> Union[dt.date, None]:
        """Returns datetime.date object from built-in datetime module"""
        return self.__date_obj

    @property
    def date_obj(self) -> Union[dt.date, None]:
        """Returns datetime.date object from built-in datetime module"""
        return self.__date_obj

    @property
    def full(self) -> str:
        """Returns full date string (Example: "February 3, 2021")"""
        return self.__full_date

    @property
    def short(self) -> str:
        """Returns short date string (Example: "Feb 3, 2021")"""
        return self.__short_date
    
    @property
    def timezone(self) -> pytz.timezone:
        return self.__tz


class MlbDatetime(MlbDate):
    def __init__(self, _datetime_string: str,tz=None):
        super().__init__(_datetime_string,tz=tz)
        dt_obj = self.dt_obj
        self.__dt_obj = dt_obj
        self.__time_obj = dt_obj.time()
        self.__time_str = self.__time_obj.strftime(r'%I:%M %p %Z')

    def __repr__(self):
        return self.__dt_obj.strftime(r'%Y-%m-%dT%H:%M:%SZ')

    def __str__(self):
        return self.__dt_obj.strftime(r'%Y-%m-%dT%H:%M:%SZ')

    def __call__(self,fmt=None) -> Union[dt.datetime,str]:
        """Returns `datetime` object from built-in datetime module.
        Alternatively, a string format can be specified instead
        
        Paramaters:
        -----------
        fmt : str
            Datetime string format to return
        """
        dt_obj = self.__dt_obj
        if fmt is None:
            return dt_obj
        else:
            return dt_obj.strftime(fmt)

    @property
    def time_obj(self):
        """Returns `time` object from built-in datetime module"""
        return self.__time_obj
    
    @property
    def time_str(self):
        """Returns ISO-formatted string representation of object"""
        return self.__time_str


class MlbWrapper:
    """### Data wrapper
    Namespace/subscriptable object for retrieving nested data

    """

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __getitem__(self, __name: str):
        return getattr(self, __name)

    def __call__(self, _attr):
        return getattr(self, _attr)


class PlayerPositionWrapper(MlbWrapper):
    def __init__(self,**kwargs):
        self.__code = kwargs.get('__code','-')
        self.__name = kwargs.get('__name','-')
        self.__type = kwargs.get('__type','-')
        self.__abbreviation = kwargs.get('__abbreviation','-')
        super().__init__(**kwargs)
        
    @property
    def code(self):
        return self.__code
    
    @property
    def name(self):
        return self.__name
    
    @property
    def type_(self):
        return self.__type
    
    @property
    def abbreviation(self):
        return self.__abbreviation
    
    abbrv = abbreviation


class EducationWrapper:
    """Education wrapper"""

    class school_data:
        def __new__(cls, school_df: pd.DataFrame):
            self = object.__new__(cls)
            self.__df = school_df
            self.__repr = tab(
                self.__df, headers="keys", tablefmt="simple", showindex=False
            )
            return self

        def __repr__(self):
            return self.__repr

        def __call__(
            self, school_attr: Optional[str] = None, row_idx: int = 0
        ) -> Union[pd.DataFrame, str]:
            """Return either a dataframe of all schools data OR a specific school attribute by providing index ('row_idx')

            Parameters:
            -----------
            school_attr : str
                School attribute (column label from dataframe) to retrieve

            row_idx : int (conditionally required) Default -> 1
                Row index to query from dataframe. Required if using 'school_attr' to return string


            ### See also:
            - 'school.name()'
            - 'school.city()'
            - 'school.state()'

            """
            if school_attr is None:
                return self.__df
            df = self.__df
            if len(df) == 1:
                row_idx = 0
            elif len(df) == 0:
                return "-"
            return df.iloc[int(row_idx)]["school"]

        def name(self, row_idx: int = 0) -> str:
            """Get school's name given an integer

            Parameters:
            -----------
            row_idx : int (required) Default -> 1
                Row index to query from dataframe
            """
            df = self.__df
            if len(df) == 1:
                row_idx = 0
            elif len(df) == 0:
                return "-"
            return df.iloc[int(row_idx)]["school"]

        def city(self, row_idx: int = 0) -> str:
            """Get school's city given an integer

            Parameters:
            -----------
            row_idx : int (required) Default -> 1
                Row index to query from dataframe
            """
            df = self.__df
            if len(df) == 1:
                row_idx = 0
            elif len(df) == 0:
                return "-"
            return df.iloc[int(row_idx)]["city"]

        def state(self, row_idx: int = 0) -> str:
            """Get school's state given an integer

            Parameters:
            -----------
            row_idx : int (required) Default -> 1
                Row index to query from dataframe
            """
            df = self.__df
            if len(df) == 1:
                row_idx = 0
            elif len(df) == 0:
                return "-"
            return df.iloc[int(row_idx)]["state"]

    def __new__(cls, edu_df: Optional[pd.DataFrame] = None):
        self = object.__new__(cls)
        hs_df = edu_df[edu_df["type"] == "highschool"]
        co_df = edu_df[edu_df["type"] == "college"]
        self.__df = edu_df
        self.__highschool = cls.school_data(school_df=hs_df)
        self.__college = cls.school_data(school_df=co_df)

        self.__repr = tab(self.__df, headers="keys", tablefmt="simple", showindex=False)

        return self

    def __repr__(self):
        return self.__repr

    def __call__(self, school_type: Optional[str] = None) -> pd.DataFrame:
        """Returns education/school dataframe by 'school_type'. If 
        'school_type' is not provided, all school data will be returned

        Parameters:
        -----------
        school_type : str (optional)
            Get data by school type ('highschool','college')

        """
        if school_type is None:
            return self.__df
        else:
            if school_type.lower() == "highschool":
                return self.__highschool
            elif school_type == "college":
                return self.__college

    @property
    def highschool(self) -> school_data:
        """Highschool data (if available)"""
        return self.__highschool

    @property
    def college(self) -> school_data:
        """College data (if available)"""
        return self.__college


class TeamNameData(MlbWrapper):
    """A wrapper containing data on a variations of a team's name"""

    def __init__(self,mlbam=None,full=None,short=None,franchise=None,location=None,club=None,season=None,abbreviation=None,**kwargs):
        super().__init__(**kwargs)
        self.__mlbam = mlbam
        self.__full = full
        self.__short = short
        self.__franchise = franchise
        self.__location = location
        self.__club = club
        self.__season = season
        self.__abbreviation = abbreviation

    def __str__(self) -> str:
        return self.__full

    def __repr__(self) -> str:
        return self.__full

    @property
    def mlbam(self):
        """Team's official MLB ID"""
        return self.__mlbam

    @property
    def full(self):
        """Team's full name"""
        return self.__full

    @property
    def short(self):
        """Team's shortened name"""
        return self.__short

    @property
    def franchise(self):
        """Team's parent franchise org name"""
        return self.__franchise

    @property
    def location(self):
        """Team's location (typically the city or state that a club is based)"""
        return self.__location

    @property
    def club(self):
        """Team's club name"""
        return self.__club

    @property
    def slug(self):
        """Team's slug name (typcally appears in the URL path of a team's 
        page on a website)
        
        """
        return self.__slug

    @property
    def season(self):
        """Season of play"""
        return self.__season
    
    @property
    def abbreviation(self):
        """Season of play"""
        return self.__abbreviation

    id_ = mlbam
    abbrv = abbreviation


class TeamSlim(MlbWrapper):
    """simple wrapper for holding small amounts of team data"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._mlbam = kwargs["mlbam"]
        self._name = dclass.TeamName(
            int(kwargs.get('mlbam',0)),
            kwargs['full'],
            kwargs['location'],
            kwargs['franchise'],
            kwargs['club'],
            kwargs['short'],
            kwargs.get('abbreviation','')
        )
        self._league = dclass.Leagues.get(kwargs['lg_mlbam'])
        self._division = dclass.Leagues.get(kwargs['div_mlbam'])
        self._venue = dclass.Venue(kwargs['venue_mlbam'],kwargs['venue_name'])

    @property
    def mlbam(self):
        """Team's official MLB ID"""
        return self._mlbam

    @property
    def name(self):
        """Various names team names/aliases"""
        return self._name

    @property
    def league(self):
        """Information for team's league

        NOTE: The 'name' attribute from the league wrapper can also be 
        retrieved by simply calling this property
        """
        
        return self._league

    @property
    def division(self):
        """Information for team's division

        NOTE: The 'name' attribute from the league wrapper can also be 
        retrieved by simply calling this property
        """
        
        return self._division

    @property
    def venue(self):
        """Info for team's venue"""
        return self._venue


class PersonNameData(MlbWrapper):
    """A wrapper containing data on a variations of a PERSON's name"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def __str__(self):
        return self.full

    def __repr__(self):
        return self.full

    @property
    def full(self):
        """Person's full name"""
        return self._full

    @property
    def given(self):
        """Person's given name (e.g. - 'FIRST MIDDLE LAST)"""
        return self._given

    @property
    def first(self):
        """Person's first name"""
        return self._first

    @property
    def middle(self):
        """Person's middle name (may sometimes just be the middle initial)
        """
        try:
            return self._middle
        except:
            return None

    @property
    def last(self):
        """Person's last name"""
        return self._last

    @property
    def nick(self):
        """Person's nick name (if player has one)"""
        try:
            return self._nick
        except:
            return None

    @property
    def pronunciation(self):
        """Pronunciation of person's LAST name"""
        try:
            return self._pronunciation
        except:
            return None


class StatGroup(MlbWrapper):
    def __init__(
        self,
        career_regular: Optional[pd.DataFrame] = None,
        career_advanced: Optional[pd.DataFrame] = None,
        season_regular: Optional[pd.DataFrame] = None,
        season_advanced: Optional[pd.DataFrame] = None,
        yby_regular: Optional[pd.DataFrame] = None,
        yby_advanced: Optional[pd.DataFrame] = None,
    ):

        self.__regular = MlbWrapper(
            career=career_regular, season=season_regular, yby=yby_regular
        )
        self.__advanced = MlbWrapper(
            career=career_advanced, season=season_advanced, yby=yby_advanced
        )

        self.__career = MlbWrapper(regular=career_regular, advanced=career_advanced)
        self.__season = MlbWrapper(regular=season_regular, advanced=season_advanced)
        self.__yby = MlbWrapper(regular=yby_regular, advanced=yby_advanced)

    @property
    def season(self):
        return self.__season

    @property
    def career(self):
        return self.__career

    @property
    def yby(self):
        return self.__yby

    @property
    def regular(self):
        return self.__regular

    @property
    def standard(self):
        return self.__regular

    @property
    def advanced(self):
        return self.__advanced


class PlayerStats(MlbWrapper):
    def __init__(
        self,
        hit_car_reg: Optional[pd.DataFrame] = None,
        hit_car_adv: Optional[pd.DataFrame] = None,
        hit_ssn_reg: Optional[pd.DataFrame] = None,
        hit_ssn_adv: Optional[pd.DataFrame] = None,
        hit_yby_reg: Optional[pd.DataFrame] = None,
        hit_yby_adv: Optional[pd.DataFrame] = None,
        pit_car_reg: Optional[pd.DataFrame] = None,
        pit_car_adv: Optional[pd.DataFrame] = None,
        pit_ssn_reg: Optional[pd.DataFrame] = None,
        pit_ssn_adv: Optional[pd.DataFrame] = None,
        pit_yby_reg: Optional[pd.DataFrame] = None,
        pit_yby_adv: Optional[pd.DataFrame] = None,
        fld_car_reg: Optional[pd.DataFrame] = None,
        fld_ssn_reg: Optional[pd.DataFrame] = None,
        fld_yby_reg: Optional[pd.DataFrame] = None,
    ):
        super().__init__(
            **{
                "hit_car_reg": hit_car_reg,
                "hit_car_adv": hit_car_adv,
                "hit_ssn_reg": hit_ssn_reg,
                "hit_ssn_adv": hit_ssn_adv,
                "hit_yby_reg": hit_yby_reg,
                "hit_yby_adv": hit_yby_adv,
                "pit_car_reg": pit_car_reg,
                "pit_car_adv": pit_car_adv,
                "pit_ssn_reg": pit_ssn_reg,
                "pit_ssn_adv": pit_ssn_adv,
                "pit_yby_reg": pit_yby_reg,
                "pit_yby_adv": pit_yby_adv,
                "fld_car_reg": fld_car_reg,
                "fld_ssn_reg": fld_ssn_reg,
                "fld_yby_reg": fld_yby_reg,
            }
        )

        self.__all_stats_dict = {
            "hitting": {
                "career": [hit_car_reg, hit_car_adv],
                "yby": [hit_yby_reg, hit_yby_adv],
                "season": [hit_ssn_reg, hit_ssn_adv],
            },
            "pitching": {
                "career": [pit_car_reg, pit_car_adv],
                "yby": [pit_yby_reg, pit_yby_adv],
                "season": [pit_ssn_reg, pit_ssn_adv],
            },
            "fielding": {
                "career": [fld_car_reg, fld_car_reg],
                "yby": [fld_yby_reg, fld_yby_reg],
                "season": [fld_ssn_reg, fld_ssn_reg],
            },
        }

        self.__hitting = StatGroup(
            career_regular=hit_car_reg,
            career_advanced=hit_car_adv,
            season_regular=hit_ssn_reg,
            season_advanced=hit_ssn_adv,
            yby_regular=hit_yby_reg,
            yby_advanced=hit_yby_adv,
        )
        self.__pitching = StatGroup(
            career_regular=pit_car_reg,
            career_advanced=pit_car_adv,
            season_regular=pit_ssn_reg,
            season_advanced=pit_ssn_adv,
            yby_regular=pit_yby_reg,
            yby_advanced=pit_yby_adv,
        )
        self.__fielding = StatGroup(
            career_regular=fld_car_reg,
            season_regular=fld_ssn_reg,
            yby_regular=fld_yby_reg,
        )

    def __call__(
        self,
        stat_group: str,
        stat_type: str,
        filter_by: Optional[str] = None,
        filter_val: Optional[str] = None,
        advanced: Union[bool, str] = False,
        **kwargs,
    ):
        """Get stats data through class call

        Parameters:
        -----------
        stat_group : str (required)
            specify a stat group ('hitting','pitching' or 'fielding')

        stat_type : str (required)
            specify a stat type ('career', 'yby', 'season')

        advanced : bool (required, Default is False)

        filter_by : str

        """
        try:
            group_dict = self.__all_stats_dict[stat_group]
            stat_selection = group_dict[stat_type]
            idx = 0
            if kwargs.get("adv") is not None:
                advanced = kwargs["adv"]
            if (advanced is True) or ("adv" in str(advanced)):
                idx = 1
            df = stat_selection[idx]

            if filter_by is not None:
                df = df[df[filter_by] == filter_val]

            return df
        except:
            if kwargs.get("exception_val") is not None:
                return kwargs["exception_val"]
            else:
                return None

    def get(
        self,
        stat_group: str,
        stat_type: str,
        filter_by: Optional[str] = None,
        filter_val: Optional[str] = None,
        advanced: Union[bool, str] = False,
        **kwargs,
    ):
        """Get stats data through class call

        Parameters:
        -----------
        stat_group : str (required)
            specify a stat group ('hitting','pitching' or 'fielding')

        stat_type : str (required)
            specify a stat type ('career', 'yby', 'season')

        advanced : bool (required, Default is False)

        filter_by : str

        filter_val : str

        """
        try:
            group_dict = self.__all_stats_dict[stat_group]
            stat_selection = group_dict[stat_type]
            idx = 0
            if kwargs.get("adv") is not None:
                advanced = kwargs["adv"]
            if (advanced is True) or ("adv" in str(advanced)):
                idx = 1
            df = stat_selection[idx]

            if filter_by is not None:
                df = df[df[filter_by] == filter_val]

            return df
        except:
            if kwargs.get("exception_val") is not None:
                return kwargs["exception_val"]
            else:
                return None

    @property
    def hitting(self):
        """Player's hitting stats (regular or advanced)"""
        return self.__hitting

    @property
    def pitching(self):
        """Player's pitching stats (regular or advanced)"""
        return self.__pitching

    @property
    def fielding(self):
        """Player's fielding stats"""
        return self.__fielding


# ===============================================================
# Parsing Utils
# ===============================================================

class _teams_data_collection:
    def __init__(self, _df: pd.DataFrame):
        self.__teams_df = _df
        self.__repr = tab(
            self.__teams_df, headers="keys", showindex=False, tablefmt="simple"
        )

    def __call__(self):
        return self.__teams_df

    def __repr__(self):
        return self.__repr

    def display(self):
        return self.__teams_df

    def find(self, query: str, season=None):
        df = self.__teams_df
        if season is not None:
            df = df[df["season"] == season]

        query = query.lower()
        found_data = []
        for idx, row in df.iterrows():
            if query in row["name_full"].lower():
                found_data.append(row)

        return pd.DataFrame(data=found_data).reset_index(drop=True)


class _people_data_collection:
    def __init__(self, _df: pd.DataFrame):
        self.__pdf = _df
        renamed_cols = {
            "pos_abbreviation": "Pos",
            "name_full": "Name",
            "draft_year": "Drafted",
            "mlb_debut": "Debut",
        }
        _df = _df[
            ["mlbam", "name_full", "pos_abbreviation", "draft_year", "mlb_debut"]
        ].rename(columns=renamed_cols)
        self.__repr = tab(_df, headers="keys", showindex=False, tablefmt="simple")

    def __call__(self) -> pd.DataFrame:
        return self.__pdf

    def __repr__(self):
        return self.__repr

    def display(self) -> pd.DataFrame:
        return self.__pdf

    def find(self, name: str, season=None) -> pd.DataFrame:
        df = self.__pdf
        # if season is not None:
        #     df = df[df['season']==season]

        name = name.lower()
        found_data = []
        for idx, row in df.iterrows():
            if name in row["name_full"].lower():
                found_data.append(row)

        return pd.DataFrame(data=found_data).reset_index(drop=True)


class MlbTeam:
    def __init__(self, raw_data: dict, **kwargs):
        self.__dict__.update(kwargs)
        self.__og_dict = kwargs
        to_display = [
            "{:20}{:<25}".format("KEYS/ATTRS", "VALUES"),
            "{:20}{:<25}".format("==================  ", "====================     "),
        ]
        for k, v in self.__og_dict.items():
            to_display.append("{:20}{:<25}".format(k, v))
        self.__repr = "\n".join(to_display)

        self.__raw_data = raw_data

    def __getitem__(self, key: str):
        # print(__name)
        return self.__og_dict[key]
        # return getattr(self,__name)

    def __str__(self):
        return self.name_full

    def __repr__(self):
        return self.__repr

    def raw_data(self):
        return self.__raw_data


class MlbPerson(MlbWrapper):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        to_display = [
            "{:20}{:<25}".format("KEYS/ATTRS", "VALUES"),
            "{:20}{:<25}".format("==================  ", "====================     "),
        ]
        for k, v in self.__dict__.items():
            to_display.append("{:20}{:<25}".format(k, v))
        self.__repr = "\n".join(to_display)

    def __getitem__(self, __name: str):
        return getattr(__name)

    def __str__(self):
        return self.name_full

    def __repr__(self):
        return self.__repr


class Umpires(MlbWrapper):
    def __init__(self,first,second,third,home):
        self.first  = first
        self.second = second
        self.third  = third
        self.home   = home
        
        ump_list = [f'First: {self.first}',f'Second: {self.second}',
                    f'Third: {self.third}',f'Home: {self.home}']
        self.__string_rep = '\n'.join(ump_list)
    
    def __str__(self):
        return self.__string_rep
    
    def __repr__(self):
        return self.__string_rep


def _parse_league(_obj: dict):
    d = _obj

def _parse_division(_obj: dict):
    d = _obj

def _parse_venue(_obj: dict):
    d = _obj

def _parse_team(_obj: dict):
    d = _obj
    spring_league = d.get("springLeague", {})
    spring_venue = d.get("springVenue", {})
    lg = d.get("league", {})
    div = d.get("division", {})
    venue = d.get("venue", {})

    data = {
        "mlbam": d.get("id", 0),
        "name_full": d.get("name", "-"),
        "team_code": d.get("teamCode", 0),
        "file_code": d.get("fileCode", 0),
        "abbreviation": d.get("abbreviation", "-"),
        "name_team": d.get("teamName", "-"),
        "name_location": d.get("locationName", "-"),
        "name_short": d.get("shortName", "-"),
        "name_franchise": d.get("franchiseName", "-"),
        "name_club": d.get("clubName", "-"),
        "season": d.get("season", 0),
        "first_year": d.get("firstYearOfPlay", "0"),
        "active": d.get("active", None),
        "all_start_status": d.get("allStarStatus"),
        "lg_mlbam": lg.get("id", 0),
        "lg_full": lg.get("name", "-"),
        "lg_abbrv": lg.get("abbreviation", "-"),
        "lg_abbreviation": lg.get("abbreviation", "-"),
        "lg_short": lg.get("nameShort", "-"),
        "div_mlbam": div.get("id", 0),
        "div_full": div.get("name", "-"),
        "div_abbrv": div.get("abbreviation", "-"),
        "div_abbreviation": div.get("abbreviation", "-"),
        "div_short": div.get("nameShort", "-"),
        "ven_mlbam": venue.get("id", 0),
        "ven_name": venue.get("name", "-"),
        "sp_ven_mlbam": spring_venue.get("id", 0),
        "sp_ven_name": spring_venue.get("name", "-"),
        "sp_lg_mlbam": spring_league.get("id", 0),
        "sp_lg_name": spring_league.get("name", "-"),
        "sp_lg_abbrv": spring_league.get("abbreviation", "-"),
    }

    return data

def get_tz(tz):
    if type(tz) is str:
        tz = tz.lower()
        if tz == 'pt':
            return pytz.timezone('US/Pacific')
        elif tz == 'mt':
            return pytz.timezone('US/Mountain')
        elif tz == 'ct':
            return pytz.timezone('US/Central')
        elif tz == 'et':
            return pytz.timezone('US/Eastern')
    else:
        return tz
    
# ===============================================================
# Dataframe functions
# ===============================================================
def add_league_attr(row:pd.Series,attr:str):
    teams = TEAMS[TEAMS['season']==int(row['season'])]
    tmrow = teams[teams['mlbam']==row['team_mlbam']].iloc[0]
    lgrow = LEAGUES[LEAGUES['mlbam']==tmrow['lg_mlbam']].iloc[0]
    return lgrow[attr]

def add_league_short(row:pd.Series):
    teams = TEAMS[TEAMS['season']==int(row['season'])]
    tmrow = teams[teams['mlbam']==row['team_mlbam']].iloc[0]
    return tmrow.lg_abbrv

def add_division_short(row:pd.Series):
    teams = TEAMS[TEAMS['season']==int(row['season'])]
    tmrow = teams[teams['mlbam']==row['team_mlbam']].iloc[0]
    div_mlbam: Union[str,int] = tmrow['div_mlbam']
    return league_ref[div_mlbam].short

def add_division_mlbam(row:pd.Series):
    teams = TEAMS[TEAMS['season']==int(row['season'])]
    tmrow = teams[teams['mlbam']==row['team_mlbam']].iloc[0]
    div_mlbam: Union[str,int] = tmrow['div_mlbam']
    return div_mlbam

def add_team_attr(row:pd.DataFrame,attr:str,season=None):
    teams = TEAMS[TEAMS['season']==int(row['season'])]
    tmrow = teams[teams['mlbam']==row['team_mlbam']].iloc[0]
    return tmrow[attr]
