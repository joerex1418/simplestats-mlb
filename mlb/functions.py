import time
import datetime as dt
import platform, requests
from urllib.parse import unquote
from typing import Union, Optional, List

import pandas as pd
import asyncio, aiohttp, nest_asyncio
from bs4 import BeautifulSoup as bs, SoupStrainer

from . import mlb_dataclasses as dclass
from . import constants as c
from . import parsing, helpers, mlbdata
from .async_mlb import fetch, _determine_loop
from .utils import curr_date, default_season, get_tzinfo
from .helpers import ExtendedDict

nest_asyncio.apply()

if platform.system() == "Windows":
    standard_time_fmt = r"%I:%M %p"
else:
    standard_time_fmt = r"%-I:%M %p"

# ===============================================================
# ASYNC
# ===============================================================

async def _parse_player_data(
    data,
    session:aiohttp.ClientSession,
    _url,
    _mlbam=None):
    if "hydrate=currentTeam" in _url:
        data = data["people"][0]
        debut = data["mlbDebutDate"]
        query = f"stats=gameLog&startDate={debut}&endDate={debut}&hydrate=team"
        resp = await session.get(f"{c.BASE}/people/{_mlbam}/stats?{query}")
        data["debut_data"] = await resp.json()
        return data
    elif type(data) is dict:
        return data
    else:
        soup = bs(data,'lxml',parse_only=SoupStrainer("a"))
        href_url = soup.find("a",text="View Player Info")["href"]
        resp = await session.get(href_url)
        bio_page = await resp.text()
        soup = bs(bio_page,'lxml',parse_only=SoupStrainer(['div','h2','p']))

        all_ps = soup.find(id="mw-content-text").find("div",class_="mw-parser-output").find("h2").find_all_next("p")
        for idx,p in enumerate(all_ps):
            all_ps[idx] = p.getText()

        return all_ps

async def _fetch_player_data(
    urls,
    _get_bio=None,
    _mlbam=None):
    retrieved_responses = []
    async with aiohttp.ClientSession() as session:
        tasks = []
        for url in urls:
            tasks.append(session.get(url, ssl=False))

        responses = await asyncio.gather(*tasks)
        
        for resp_idx, response in enumerate(responses):
            if resp_idx == 0 and _get_bio is True:
                resp = await response.text()
            else:
                resp = await response.json()
            
            parsed_data = await _parse_player_data(data=resp,session=session,_url=str(response.url),_mlbam=_mlbam)

            retrieved_responses.append(parsed_data)
        
        await session.close()
    
    return retrieved_responses

async def _parse_team_data(
    data,session:aiohttp.ClientSession,
    _url,
    lgs_df:pd.DataFrame,
    _mlbam,
    **kwargs):
    start = time.time()
    if f"/teams/{_mlbam}?season=" in _url:
        team_info_parsed = {}
        teams : dict = data["teams"][0]
        lg_mlbam  = teams.get("league",{}).get("id",0)
        lg_row      = lgs_df.loc[int(lg_mlbam)]
        div_mlbam = teams.get("division",{}).get("id",0)
        div_row     = lgs_df.loc[int(div_mlbam)]
        team_info_parsed["mlbam"]               = teams["id"]
        team_info_parsed["full_name"]           = teams["name"]
        team_info_parsed["location_name"]       = teams["locationName"]
        team_info_parsed["franchise_name"]      = teams["franchiseName"]
        team_info_parsed["team_name"]           = teams["teamName"]
        team_info_parsed["club_name"]           = teams["clubName"]
        team_info_parsed["short_name"]          = teams["shortName"]
        team_info_parsed["venue_mlbam"]         = teams.get("venue",{}).get("id","")
        team_info_parsed["venue_name"]          = teams.get("venue",{}).get("name","")
        team_info_parsed["first_year"]          = teams["firstYearOfPlay"]
        team_info_parsed["league_mlbam"]        = lg_mlbam
        team_info_parsed["league_name"]         = lg_row["name_full"]
        team_info_parsed["league_short"]        = lg_row["name_short"]
        team_info_parsed["league_abbrv"]        = lg_row["abbreviation"]
        team_info_parsed["div_mlbam"]           = div_mlbam
        team_info_parsed["div_name"]            = div_row["name_full"]
        team_info_parsed["div_short"]           = div_row["name_short"]
        team_info_parsed["div_abbrv"]           = div_row["abbreviation"]
        team_info_parsed["season"]              = teams["season"]

        return team_info_parsed

    elif "/schedule?sportId=1&teamId=" in _url:
        sched_data = []
        dates_dict_array : List[dict] = data.get("dates",[{}])
        for d in dates_dict_array:
            date_obj = dt.datetime.strptime(d["date"],r"%Y-%m-%d")
            games : List[dict] = d["games"]
            for gm in games:
                away        = gm.get("teams",{}).get("away")
                home        = gm.get("teams",{}).get("home")
                away_obj    = away.get("team",{})
                home_obj    = home.get("team",{})

                aw_lg_mlbam = away_obj.get('league',{}).get('id',0)
                aw_lg_row = lgs_df.loc[int(aw_lg_mlbam)]
                aw_div_mlbam = away_obj.get('division',{}).get('id',0)
                aw_div_row = lgs_df.loc[int(aw_div_mlbam)]


                hm_lg_mlbam = away_obj.get('league',{}).get('id',0)
                hm_lg_row = lgs_df.loc[int(hm_lg_mlbam)]
                hm_div_mlbam = away_obj.get('division',{}).get('id',0)
                hm_div_row = lgs_df.loc[int(hm_div_mlbam)]

                is_win = False
                is_home = True if home_obj.get("id") == int(_mlbam) else False
                if home_obj.get("id") == int(_mlbam):
                    is_home = True
                    if home.get('isWinner') is True:
                        is_win = True
                else:
                    is_home = False
                    if away.get('isWinner') is True:
                        is_win = True


                venue = gm.get('venue',{})
                status = gm.get('status',{})
                gamePk = gm.get("gamePk")

                recap_title = ""
                recap_desc = ""
                recap_url = ""
                recap_avail = False
                media = gm.get("content",{}).get("media",{})
                epgAlt = media.get("epgAlternate",[{}])
                for e in epgAlt:
                    if e.get("title") == "Daily Recap":
                        epg_items = e.get("items")
                        gotUrl = False
                        for i in epg_items:
                            recap_title = i.get("title","")
                            recap_desc = i.get("description")
                            for p in i.get("playbacks",[{}]):
                                playback_type = p.get("name")
                                if playback_type == "mp4Avc" or playback_type == "highBit":
                                    recap_url = p.get("url")
                                    gotUrl = True
                                    recap_avail = True
                                    break
                            if gotUrl is True:
                                break

                sched_data.append([
                    gm.get("season"),
                    date_obj,
                    gamePk,
                    gm.get("gameType"),
                    status.get('abstractGameState','-'),
                    status.get('detailedState','-'),
                    is_home,
                    is_win,
                    away_obj.get("id"),
                    away_obj.get("name"),
                    away_obj.get("locationName"),
                    away_obj.get("franchiseName"),
                    away_obj.get("clubName"),
                    aw_lg_mlbam,
                    aw_lg_row['name_full'],
                    aw_lg_row['name_short'],
                    aw_lg_row['abbreviation'],
                    aw_div_mlbam,
                    aw_div_row['name_full'],
                    aw_div_row['name_short'],
                    aw_div_row['abbreviation'],
                    away.get("score",0),
                    home_obj.get("id"),
                    home_obj.get("name"),
                    home_obj.get("locationName"),
                    home_obj.get("franchiseName"),
                    home_obj.get("clubName"),
                    hm_lg_mlbam,
                    hm_lg_row['name_full'],
                    hm_lg_row['name_short'],
                    hm_lg_row['abbreviation'],
                    hm_div_mlbam,
                    hm_div_row['name_full'],
                    hm_div_row['name_short'],
                    hm_div_row['abbreviation'],
                    home.get("score",0),
                    gm.get('gameNumber'),
                    False if gm.get("doubleHeader") == "N" else True,
                    gm.get("seriesGameNumber"),
                    gm.get("gamesInSeries"),
                    gm.get('seriesDescription'),
                    gm.get('scheduledInnings'),
                    gm.get('rescheduleGameDate'),
                    gm.get('rescheduledFromDate'),
                    venue.get('id','-'),
                    venue.get('name','-'),
                    recap_title,
                    recap_desc,
                    recap_url,
                    recap_avail,
                ])
        sched_df = pd.DataFrame(data=sched_data,
                                columns=['season',
                                         'date',
                                         'gamePk',
                                         'game_type',
                                         'status_abstract',
                                         'status_detailed',
                                         'is_home',
                                         'is_win',
                                         'away_mlbam',
                                         'away_name',
                                         'away_location',
                                         'away_franchise',
                                         'away_club',
                                         'away_lg_mlbam',
                                         'away_lg_name',
                                         'away_lg_short',
                                         'away_lg_abbrv',
                                         'away_div_mlbam',
                                         'away_div_name',
                                         'away_div_short',
                                         'away_div_abbrv',
                                         'away_score',
                                         'home_mlbam',
                                         'home_name',
                                         'home_location',
                                         'home_franchise',
                                         'home_club',
                                         'home_lg_mlbam',
                                         'home_lg_name',
                                         'home_lg_short',
                                         'home_lg_abbrv',
                                         'home_div_mlbam',
                                         'home_div_name',
                                         'home_div_short',
                                         'home_div_abbrv',
                                         'home_score',
                                         'day_game_number',
                                         'double_header',
                                         'series_game',
                                         'series_length',
                                         'series_description',
                                         'scheduled_inns',
                                         'reschedule_date_to',
                                         'rescheduled_date_from',
                                         'venue_mlbam',
                                         'venue_name',
                                         'recap_title',
                                         'recap_desc',
                                         'recap_url',
                                         'recap_avail',
                                         ])

        if kwargs.get('_logtime') is True:
            print(f"\n{unquote(_url).replace(c.BASE,'')}")
            print(f'--- {time.time() - start} seconds ---\n')
        return sched_df
    
    elif "statSplits" in _url and "/roster/" in _url:
        stat_data = []

        roster : List[dict] = data['roster']

        for roster_entry in roster:
            # p = roster_entry.get('person',{})
            p = roster_entry['person']
            mlbam           = p.get('id',0)
            name            = p.get('fullName','-')
            jersey_number   = roster_entry.get('jerseyNumber','-')
            position : str  = roster_entry.get('position',{}).get('abbreviation','-')
            slug     : str  = p.get('nameSlug',str(mlbam))
            status   : str  = p.get('status',{}).get('description','-')

            pstats = p.get('stats')
            if pstats is None:
                continue

            for stat_item in pstats:
                splits = stat_item.get("splits",[{}])

                for s in splits:
                    stat = s.get("stat",{})
                    try:
                        team = s['team']
                    except:
                        team = {}
                    tm_mlbam    = team.get("id","")
                    tm_name     = team.get("name","")

                    stat['season']      = s.get('season','-')
                    stat['mlbam']       = mlbam
                    stat['name']        = name
                    stat['slug']        = slug
                    stat['tm_mlbam']    = tm_mlbam
                    stat['tm_name']     = tm_name

                    stat['jersey_number'] = jersey_number
                    stat['position']      = position
                    stat['game_type']     = s.get('gameType')
                    stat['ptype']         = s.get('split',{}).get('code','P').upper()

                    stat_data.append(pd.Series(stat))
        added_cols = ['season','mlbam','name','slug','jersey_number','ptype','pos','game_type','tm_mlbam','tm_name']

        if 'Advanced' in _url:
            reordered_cols  = added_cols + c.COLS_PIT_ADV
        else:
            reordered_cols  = added_cols + c.COLS_PIT

        try:
            combined_df = pd.DataFrame(stat_data).rename(columns=c.STATDICT).reindex(columns=reordered_cols)
        except:
            combined_df = pd.DataFrame(stat_data).rename(columns=c.STATDICT)
            # print(pd.DataFrame(stat_data).rename(columns=c.STATDICT).columns)

        if kwargs.get('_logtime') is True:
            print(f"\n{unquote(_url).replace(c.BASE,'')}")
            print(f'--- {time.time() - start} seconds ---\n')

        return combined_df

    elif "/roster/coach?season=" in _url:
        coach_data = []
        roster : List[dict] = data.get('roster',[{}])
        for roster_entry in roster:
            job                 = roster_entry.get('job','-')
            job_title           = roster_entry.get('title','-')
            job_id              = roster_entry.get('jobId','-')
            jersey_number_coach = roster_entry.get('jerseyNumber','-')

            p = roster_entry.get('person',{})
            
            coach_data.append([
                job,
                job_title,
                job_id,
                jersey_number_coach,
                p.get('primaryNumber','-'),
                p.get('fullName'),
                p.get('birthDate','-'),
                p.get('currentAge'),
                p.get('primaryPosition',{}).get('abbreviation'),
                p.get('mlbDebutDate','-'),
                p.get('lastPlayedDate','-'),
            ])
        df = pd.DataFrame(
            data=coach_data,
            columns=['job','job_title','job_id','jersey_number_coach','jersey_number_primary','name','birth_date','age','pos','mlb_debut','last_played'])

        if kwargs.get('_logtime') is True:
            print(f"\n{unquote(_url).replace(c.BASE,'')}")
            print(f'--- {time.time() - start} seconds ---\n')

        return df

    elif "/roster/" in _url:
        stat_data = []

        roster : List[dict] = data['roster']

        for roster_entry in roster:
            # p = roster_entry.get('person',{})
            p = roster_entry['person']
            mlbam           = p.get('id',0)
            name            = p.get('fullName','-')
            jersey_number   = roster_entry.get('jerseyNumber','-')
            position : str  = roster_entry.get('position',{}).get('abbreviation','-')
            slug     : str  = p.get('nameSlug',str(mlbam))
            status   : str  = p.get('status',{}).get('description','-')

            pstats = p.get('stats')
            if pstats is None:
                continue

            for stat_item in pstats:
                splits = stat_item.get("splits",[{}])

                for s in splits:
                    stat = s.get("stat",{})
                    try:
                        team = s['team']
                    except:
                        team = {}
                    tm_mlbam    = team.get("id","")
                    tm_name     = team.get("name","")

                    stat['season']      = s.get('season','-')
                    stat['mlbam']       = mlbam
                    stat['name']        = name
                    stat['slug']        = slug
                    stat['tm_mlbam']    = tm_mlbam
                    stat['tm_name']     = tm_name

                    stat['jersey_number'] = jersey_number
                    stat['position']      = position
                    stat['game_type']     = s.get('gameType')

                    stat_data.append(pd.Series(stat))

        pre_cols = ['season','mlbam','name','slug','jersey_number','pos','game_type','tm_mlbam','tm_name']
        if 'hitting' in _url:
            if 'Advanced' in _url:
                reordered_cols = pre_cols + c.COLS_HIT_ADV
            else:
                reordered_cols = pre_cols + c.COLS_HIT
        elif 'pitching' in _url:
            if 'Advanced' in _url:
                reordered_cols = pre_cols + c.COLS_PIT_ADV
            else:
                reordered_cols = pre_cols + c.COLS_PIT
        elif 'fielding' in _url:
            reordered_cols = pre_cols + c.COLS_FLD
        # print(reordered_cols)
        # print("line 480")
        try:
            combined_df = pd.DataFrame(stat_data).rename(columns=c.STATDICT).reindex(columns=reordered_cols)
        except:
            combined_df = pd.DataFrame(stat_data).rename(columns=c.STATDICT)
            # print(pd.DataFrame(stat_data).rename(columns=c.STATDICT).columns)
            
        if kwargs.get('_logtime') is True:
            print(f"\n{unquote(_url).replace(c.BASE,'')}")
            print(f'--- {time.time() - start} seconds ---\n')
        return combined_df

    elif f"/teams/{_mlbam}/stats?stats=season,seasonAdvanced" in _url:
        stat_dict = {'regular':pd.DataFrame(),'advanced':pd.DataFrame()}
        if data.get('message') is None:
            if 'gameType=S' in _url:
                gt = 'S'
            elif 'gameType=R' in _url:
                gt = 'R'
            elif 'gameType=P' in _url:
                gt = 'P'

            for stat_item in data['stats']:
                stat_data = []
                st = stat_item.get('type',{}).get('displayName')
                splits = stat_item.get('splits',[{}])
                
                for s in splits:
                    stat = s.get('stat',{})
                    stat_data.append(pd.Series(stat))

                    if st == 'seasonAdvanced':
                        add_to = 'advanced'
                        if 'hitting' in _url:
                            reordered_cols = c.COLS_HIT_ADV
                        elif 'pitching' in _url:
                            reordered_cols = c.COLS_PIT_ADV

                    else:
                        add_to = 'regular'
                        if 'hitting' in _url:
                            reordered_cols = c.COLS_HIT
                        elif 'pitching' in _url:
                            reordered_cols = c.COLS_PIT
                        elif 'fielding' in _url:
                            reordered_cols = c.COLS_FLD


                try:
                    df = pd.DataFrame(stat_data).rename(columns=c.STATDICT).reindex(columns=reordered_cols)
                except:
                    df = pd.DataFrame(stat_data).rename(columns=c.STATDICT)
                    # print(pd.DataFrame(stat_data).rename(columns=c.STATDICT).columns)

                df['game_type'] = gt

                stat_dict[add_to] = df
            
            if kwargs.get('_logtime') is True:
                print(f"\n{unquote(_url).replace(c.BASE,'')}")
                print(f'--- {time.time() - start} seconds ---\n')

            return stat_dict
        else:
            return stat_dict

    elif "/draft" in _url:
        draft_data = []
        drafts = data.get('drafts',{})
        rounds = drafts.get('rounds',[{}])

        for r in rounds:
            picks       = r.get('picks',[{}])
            for pick in picks:
                home    = pick.get('home',{})
                school  = pick.get('school',{})
                team    = pick.get('team',{})
                p       = pick.get('person',{})

                draft_type = pick.get('draftType',{})

                draft_data.append([
                    pick.get('year'),
                    pick.get('bisPlayerId',0),
                    p.get('id'),
                    p.get('fullName'),
                    p.get('birthDate'),
                    p.get('birthCity'),
                    p.get('birthStateProvince'),
                    p.get('birthCountry'),
                    p.get('height','-'),
                    p.get('weight',0),
                    p.get('primaryPosition',{}).get('abbreviation','-'),
                    p.get('batSide',{}).get('code','-'),
                    p.get('pitchHand',{}).get('code','-'),
                    pick.get('rank',0),
                    pick.get('pickRound','-'),
                    pick.get('pickNumber',0),
                    pick.get('roundPickNumber',0),
                    pick.get('pickValue','-'),
                    pick.get('signingBonus','-'),
                    home.get('city'),
                    home.get('state'),
                    home.get('country'),
                    school.get('name'),
                    school.get('schoolClass'),
                    school.get('state'),
                    school.get('country'),
                    pick.get('scoutingReport','-'),
                    pick.get('headshotLink'),
                    pick.get('blurb'),
                    pick.get('isDrafted',False),
                    pick.get('isPass',False),
                    draft_type.get('code','-'),
                    draft_type.get('description','-')
                ])

        df = pd.DataFrame(
            data=draft_data,
            columns=['season','bisID','mlbam','name','birth_date','birth_city','birth_state','birth_country','height','weight','pos','bats','throws','rank','round','pick_number','round_pick_number','value','signing_bonus','home_city','home_state','home_country','school_name','school_class','school_state','school_country','scouting_report','headshot_url','blurb','is_drafted','is_pass','draft_code','draft_description'])
        return df

    elif "/transactions" in _url:
        transactions = data.get('transactions',[{}])
        try:
            trx_columns = ("name","mlbam","tr_type","tr","description","date","e_date","r_date","fr","fr_mlbam","to","to_mlbam")
            trx_data = []
            for t in transactions:
                person  = t.get("person",{})
                p_name  = person.get("fullName","")
                p_mlbam = person.get("id","")
                typeCode = t.get("typeCode","")
                typeTr   = t.get("typeDesc")
                desc     = t.get("description")

                fr = t.get("fromTeam",{})
                fromTeam        = fr.get("name","-")
                fromTeam_mlbam  = fr.get("id","-")

                to = t.get("toTeam",{})
                toTeam          = to.get("name","-")
                toTeam_mlbam    = to.get("id","-")

                date = t.get("date","--")
                eDate = t.get("effectiveDate","--")
                rDate = t.get("resolutionDate","--")

                row = [p_name,p_mlbam,typeCode,typeTr,desc,date,eDate,rDate,fromTeam,fromTeam_mlbam,toTeam,toTeam_mlbam]
                
                trx_data.append(row)

            df = pd.DataFrame(data=trx_data,columns=trx_columns)
        except:
            df = pd.DataFrame()


        if kwargs.get('_logtime') is True:
            print(f"\n{unquote(_url).replace(c.BASE,'')}")
            print(f'--- {time.time() - start} seconds ---\n')

        return df

    if kwargs.get('_logtime') is True:
        print(f"\n{unquote(_url).replace(c.BASE,'')}")
        print(f'--- {time.time() - start} seconds ---\n')

    return data

async def _fetch_team_data(
    urls:list,
    lgs_df:pd.DataFrame,
    _mlbam,
    _logtime=None):
    retrieved_responses = []
    async with aiohttp.ClientSession() as session:
        tasks = []
        for url in urls:
            tasks.append(session.get(url, ssl=False))

        responses = await asyncio.gather(*tasks)
        
        for response in responses:
            resp = await response.json()
            
            parsed_data = await _parse_team_data(
                data=resp,
                session=session,
                _url=str(response.url),
                lgs_df=lgs_df,
                _mlbam=_mlbam,
                _logtime=_logtime)

            retrieved_responses.append(parsed_data)
        
        await session.close()
    
    return retrieved_responses

# ===============================================================
# Bulk Retrieval
# ===============================================================

def _team_data(_mlbam,_season,**kwargs) -> Union[dict,list]:
    start = time.time()
    lgs_df = mlbdata.get_leagues_df().set_index('mlbam')
    tms_df = mlbdata.mlbdata.get_teams_df()
    ssn_df = mlbdata.get_seasons_df().set_index('season')
    ssn_row = ssn_df.loc[int(_season)]

    tms_df = tms_df[tms_df['season']==int(_season)]
    
    ssn_start : pd.Timestamp = ssn_row['seasonStartDate']
    ssn_end   : pd.Timestamp = ssn_row['seasonEndDate']
    ssn_start = ssn_start.strftime(r"%Y-%m-%d")
    ssn_end   = ssn_end.strftime(r"%Y-%m-%d")

    # Retrieves 'fullSeason' roster
    # -----------------------------
    # url_list = [
    #     f"{c.BASE}/teams/{_mlbam}?season={_season}&hydrate=standings",
    #     f"{c.BASE}/teams/{_mlbam}/roster/fullSeason?season={_season}&hydrate=person(stats(type=[season],group=[hitting],season={_season}))",
    #     f"{c.BASE}/teams/{_mlbam}/roster/fullSeason?season={_season}&hydrate=person(stats(type=[season],group=[pitching],season={_season}))",
    #     f"{c.BASE}/teams/{_mlbam}/roster/fullSeason?season={_season}&hydrate=person(stats(type=[season],group=[fielding],season={_season}))",
    #     f"{c.BASE}/teams/{_mlbam}/roster/fullSeason?season={_season}&hydrate=person(stats(type=[seasonAdvanced],group=[hitting],season={_season}))",
    #     f"{c.BASE}/teams/{_mlbam}/roster/fullSeason?season={_season}&hydrate=person(stats(type=[seasonAdvanced],group=[pitching],season={_season}))",

    #     f"{c.BASE}/teams/{_mlbam}/roster/fullSeason?season={_season}&hydrate=person(stats(type=[statSplits],group=[pitching],sitCodes=[sp,rp],season={_season}))",
    #     f"{c.BASE}/teams/{_mlbam}/roster/fullSeason?season={_season}&hydrate=person(stats(type=[statSplitsAdvanced],group=[pitching],sitCodes=[sp,rp],season={_season}))",

    #     f"{c.BASE}/teams/{_mlbam}/stats?stats=season,seasonAdvanced&group=hitting&season={_season}",
    #     f"{c.BASE}/teams/{_mlbam}/stats?stats=season,seasonAdvanced&group=pitching&season={_season}",
    #     f"{c.BASE}/teams/{_mlbam}/stats?stats=season,seasonAdvanced&group=fielding&season={_season}",
    #     f"{c.BASE}/teams/{_mlbam}/roster/coach?season={_season}&hydrate=person",
    #     f"{c.BASE}/draft/{_season}?sportId=1&teamId={_mlbam}",
    #     f"{c.BASE}/transactions?teamId={_mlbam}&startDate={ssn_start}&endDate={ssn_end}",
    # ]
    
    # Retrieves '40Man' roster
    # -----------------------------
    url_list = [
        f"{c.BASE}/teams/{_mlbam}?season={_season}&hydrate=standings",
        f"{c.BASE}/teams/{_mlbam}/roster/40Man?season={_season}&hydrate=person(stats(type=[season],group=[hitting],season={_season}))",
        f"{c.BASE}/teams/{_mlbam}/roster/40Man?season={_season}&hydrate=person(stats(type=[season],group=[pitching],season={_season}))",
        f"{c.BASE}/teams/{_mlbam}/roster/40Man?season={_season}&hydrate=person(stats(type=[season],group=[fielding],season={_season}))",
        f"{c.BASE}/teams/{_mlbam}/roster/40Man?season={_season}&hydrate=person(stats(type=[seasonAdvanced],group=[hitting],season={_season}))",
        f"{c.BASE}/teams/{_mlbam}/roster/40Man?season={_season}&hydrate=person(stats(type=[seasonAdvanced],group=[pitching],season={_season}))",

        f"{c.BASE}/teams/{_mlbam}/roster/40Man?season={_season}&hydrate=person(stats(type=[statSplits],group=[pitching],sitCodes=[sp,rp],season={_season}))",
        f"{c.BASE}/teams/{_mlbam}/roster/40Man?season={_season}&hydrate=person(stats(type=[statSplitsAdvanced],group=[pitching],sitCodes=[sp,rp],season={_season}))",

        # Stats for 'gameType = S' (Spring Training)
        f"{c.BASE}/teams/{_mlbam}/stats?stats=season,seasonAdvanced&group=hitting&gameType=S&season={_season}",
        f"{c.BASE}/teams/{_mlbam}/stats?stats=season,seasonAdvanced&group=pitching&gameType=S&season={_season}",
        f"{c.BASE}/teams/{_mlbam}/stats?stats=season,seasonAdvanced&group=fielding&gameType=S&season={_season}",
        
        # Stats for 'gameType = R' (Regular Season)
        f"{c.BASE}/teams/{_mlbam}/stats?stats=season,seasonAdvanced&group=hitting&gameType=R&season={_season}",
        f"{c.BASE}/teams/{_mlbam}/stats?stats=season,seasonAdvanced&group=pitching&gameType=R&season={_season}",
        f"{c.BASE}/teams/{_mlbam}/stats?stats=season,seasonAdvanced&group=fielding&gameType=R&season={_season}",
        
        # Stats for 'gameType = P' (Postseason/Playoffs)
        f"{c.BASE}/teams/{_mlbam}/stats?stats=season,seasonAdvanced&group=hitting&gameType=P&season={_season}",
        f"{c.BASE}/teams/{_mlbam}/stats?stats=season,seasonAdvanced&group=pitching&gameType=P&season={_season}",
        f"{c.BASE}/teams/{_mlbam}/stats?stats=season,seasonAdvanced&group=fielding&gameType=P&season={_season}",
        
        f"{c.BASE}/teams/{_mlbam}/roster/coach?season={_season}&hydrate=person",
        f"{c.BASE}/draft/{_season}?sportId=1&teamId={_mlbam}",
        f"{c.BASE}/transactions?teamId={_mlbam}&startDate={ssn_start}&endDate={ssn_end}",
    ]

    sched_hydrations = "game(content(media(epg))),team"
    for m in range(12):
        month = int(m) + 1
        ssn = int(_season)
        date_obj_1 = dt.date(year=ssn,month=month,day=1)
        if month != 12:
            date_obj_2 = dt.date(year=ssn,month=month+1,day=1)
        else:
            ssn = ssn + 1
            date_obj_2 = dt.date(year=ssn,month=1,day=1)
        date_obj_2 = date_obj_2 - dt.timedelta(days = 1)
        start_date_query = f"startDate={date_obj_1.strftime(r'%Y-%m-%d')}"
        end_date_query = f"endDate={date_obj_2.strftime(r'%Y-%m-%d')}"
        date_range_query = f"{start_date_query}&{end_date_query}"
        url_to_add = f"{c.BASE}/schedule?sportId=1&teamId={_mlbam}&season={_season}&{date_range_query}&gameType={c.GAME_TYPES_ALL}&hydrate={sched_hydrations}"

        url_list.append(url_to_add)
    _logtime = kwargs.get('_logtime')
    
    # Generator comprehension
    url_list = (url for url in url_list)
    
    loop = _determine_loop()
    
    team_data_dict = loop.run_until_complete(_fetch_team_data(urls=url_list,lgs_df=lgs_df,_mlbam=_mlbam,_logtime=_logtime))
    
    total_hitting_S  = team_data_dict[8]
    total_pitching_S = team_data_dict[9]
    total_fielding_S = team_data_dict[10]
    
    total_hitting_R  = team_data_dict[11]
    total_pitching_R = team_data_dict[12]
    total_fielding_R = team_data_dict[13]
    
    total_hitting_P  = team_data_dict[14]
    total_pitching_P = team_data_dict[15]
    total_fielding_P = team_data_dict[16]
    
    total_hitting  = {'regular':pd.concat(
                                    [total_hitting_S['regular'],
                                    total_hitting_R['regular'],
                                    total_hitting_P['regular']]).reset_index(drop=True),
                      'advanced':pd.concat([total_hitting_S['advanced'],total_hitting_R['advanced'],total_hitting_P['advanced']]).reset_index(drop=True)
                      }
    total_pitching = {'regular':pd.concat([total_pitching_S['regular'],total_pitching_R['regular'],total_pitching_P['regular']]).reset_index(drop=True),
                      'advanced':pd.concat([total_pitching_S['advanced'],total_pitching_R['advanced'],total_pitching_P['advanced']]).reset_index(drop=True)
                      }
    total_fielding = {'regular':pd.concat([total_fielding_S['regular'],total_fielding_R['regular'],total_fielding_P['regular']]).reset_index(drop=True)
                      }
    
    fetched_data = {
        'team_info'    : team_data_dict[0],
        'hitting_reg'  : team_data_dict[1],
        'pitching_reg' : team_data_dict[2],
        'fielding_reg' : team_data_dict[3],
        'hitting_adv'  : team_data_dict[4],
        'pitching_adv' : team_data_dict[5],
        'p_splits_reg' : team_data_dict[6],
        'p_splits_adv' : team_data_dict[7],
        
        'total_hitting_reg' : total_hitting['regular'],
        'total_pitching_reg': total_pitching['regular'],
        'total_fielding_reg': total_fielding['regular'],
        'total_hitting_adv' : total_hitting['advanced'],
        'total_pitching_adv': total_pitching['advanced'],
        
        'coaches'           : team_data_dict[-15],
        'drafts'            : team_data_dict[-14],
        'transactions'      : team_data_dict[-13],
        'schedule'     : pd.concat(team_data_dict[-12:]),
    }

    if kwargs.get('_logtime') is True:
        print("\n\nTOTAL:")
        print(f"--- {time.time() - start} seconds ---")

    return fetched_data

def _player_data(_mlbam,**kwargs) -> dict:
    """Fetch a variety of player information/stats in one API call

    Parameters
    ----------
    mlbam : str or int
        Player's official "MLB Advanced Media" ID
    
    """
    
    pdf = mlbdata.get_people_df().set_index("mlbam").loc[_mlbam]
    tdf = mlbdata.mlbdata.get_teams_df()
    lg_df = mlbdata.get_leagues_df().set_index("mlbam")

    url_list = []

    statType = "career,careerAdvanced,yearByYear,yearByYearAdvanced"
    seasonQuery = ""

    statGroup = "hitting,pitching,fielding"
    hydrations = "currentTeam,rosterEntries(team),education,draft"
    if kwargs.get("_get_bio") is True:
        url_list.append(f"https://www.baseball-reference.com/redirect.fcgi?player=1&mlb_ID={_mlbam}")       # player_bio
    query = f"stats={statType}&gameType=R,P&group={statGroup}{seasonQuery}"
    url_list.append(c.BASE + f"/people/{_mlbam}/stats?{query}")                                               # player_stats
    url_list.append(c.BASE + f"/people/{_mlbam}/awards")                                                      # player_awards
    url_list.append(c.BASE + f"/transactions?playerId={_mlbam}")                                              # player_transactions
    url_list.append(c.BASE + f"/people/{_mlbam}?&appContext=majorLeague&hydrate={hydrations}")                # player_info
    
    # Generator attempt
    url_list = (url for url in url_list)
    loop = _determine_loop()
    responses = loop.run_until_complete(_fetch_player_data(url_list,_get_bio=kwargs.get("_get_bio"),_mlbam=_mlbam))
    if kwargs.get("_get_bio") is True:
        _player_bio     = responses[-5]
    else:
        _player_bio     = [""]
    player_stats        = responses[-4]["stats"]
    player_awards       = responses[-3]["awards"]
    player_transactions = responses[-2]["transactions"]
    player_info         = responses[-1]

    education           = player_info.get("education",{})
    roster_entries      = player_info.get("rosterEntries",[{}])
    draft               = player_info.get("drafts",[{}])
    currentTeam         = player_info.get("currentTeam",{})

    # Parsing 'player_stats'
    hitting = {
        "career":pd.DataFrame(),
        "career_advanced":pd.DataFrame(),
        "yby":pd.DataFrame(),
        "yby_advanced":pd.DataFrame()
        }
    pitching = {
        "career":pd.DataFrame(),
        "career_advanced":pd.DataFrame(),
        "yby":pd.DataFrame(),
        "yby_advanced":pd.DataFrame()
        }
    fielding = {
        "career":pd.DataFrame(),
        "yby":pd.DataFrame()
        }

    df_keys_dict = {
            "0":c.WO_SEASON+c.COLS_HIT,
            "1":c.WO_SEASON+c.COLS_HIT_ADV,
            "2":c.WO_SEASON+c.COLS_PIT,
            "3":c.WO_SEASON+c.COLS_PIT_ADV,
            "4":c.WO_SEASON+c.COLS_FLD,
            "5":c.W_SEASON+c.COLS_HIT,
            "6":c.W_SEASON+c.COLS_HIT_ADV,
            "7":c.W_SEASON+c.COLS_PIT,
            "8":c.W_SEASON+c.COLS_PIT_ADV,
            "9":c.W_SEASON+c.COLS_FLD
        }
    stat_dict = {
        "career_hit":pd.DataFrame(),
        "career_hit_adv":pd.DataFrame(),
        "career_pit":pd.DataFrame(),
        "career_pit_adv":pd.DataFrame(),
        "career_fld":pd.DataFrame(),
        "yby_hit":pd.DataFrame(),
        "yby_hit_adv":pd.DataFrame(),
        "yby_pit":pd.DataFrame(),
        "yby_pit_adv":pd.DataFrame(),
        "yby_fld":pd.DataFrame(),
    }

    for stat_item in player_stats:
        st = stat_item.get("type",{}).get("displayName")
        sg = stat_item.get("group",{}).get("displayName")
        splits = stat_item.get("splits",[{}])
        if st == "career" and sg == "hitting":
            data = []
            for s in splits:
                stats = s.get("stat")
                
                team = s.get("team",{})
                league = s.get("league",{})
                game_type = s.get("gameType")

                tm_mlbam = team.get("id","")
                tm_name = team.get("name","")
                lg_mlbam = league.get("id","")
                lg_name = league.get("name","")

                stats["game_type"] = game_type
                stats["tm_mlbam"] = tm_mlbam
                stats["tm_name"] = tm_name
                stats["lg_mlbam"] = lg_mlbam
                stats["lg_name"] = lg_name

                data.append(pd.Series(stats))
            df = pd.DataFrame(data=data).rename(columns=c.STATDICT)#[c.WO_SEASON+c.COLS_HIT]
            stat_dict["career_hit"] = df
            hitting["career"] = df

        elif st == "careerAdvanced" and sg == "hitting":
            data = []
            for s in splits:
                stats = s.get("stat")
                
                team = s.get("team",{})
                league = s.get("league",{})
                game_type = s.get("gameType")

                tm_mlbam = team.get("id","")
                tm_name = team.get("name","")
                lg_mlbam = league.get("id","")
                lg_name = league.get("name","")

                stats["game_type"] = game_type
                stats["tm_mlbam"] = tm_mlbam
                stats["tm_name"] = tm_name
                stats["lg_mlbam"] = lg_mlbam
                stats["lg_name"] = lg_name

                data.append(pd.Series(stats))
            df = pd.DataFrame(data=data).rename(columns=c.STATDICT)#[c.WO_SEASON+c.COLS_HIT_ADV]
            stat_dict["career_hit_adv"] = df
            hitting["career_advanced"] = df

        elif st == "career" and sg == "pitching":
            data = []
            for s in splits:
                stats = s.get("stat")
                
                team = s.get("team",{})
                league = s.get("league",{})
                game_type = s.get("gameType")

                tm_mlbam = team.get("id","")
                tm_name = team.get("name","")
                lg_mlbam = league.get("id","")
                lg_name = league.get("name","")

                stats["game_type"] = game_type
                stats["tm_mlbam"] = tm_mlbam
                stats["tm_name"] = tm_name
                stats["lg_mlbam"] = lg_mlbam
                stats["lg_name"] = lg_name

                data.append(pd.Series(stats))
            df = pd.DataFrame(data=data).rename(columns=c.STATDICT)#[c.WO_SEASON+c.COLS_PIT]
            stat_dict["career_pit"] = df
            pitching["career"] = df

        elif st == "careerAdvanced" and sg == "pitching":
            data = []
            for s in splits:
                stats = s.get("stat")
                
                team = s.get("team",{})
                league = s.get("league",{})
                game_type = s.get("gameType")

                tm_mlbam = team.get("id","")
                tm_name = team.get("name","")
                lg_mlbam = league.get("id","")
                lg_name = league.get("name","")

                stats["game_type"] = game_type
                stats["tm_mlbam"] = tm_mlbam
                stats["tm_name"] = tm_name
                stats["lg_mlbam"] = lg_mlbam
                stats["lg_name"] = lg_name

                data.append(pd.Series(stats))
            df = pd.DataFrame(data=data).rename(columns=c.STATDICT)#[c.WO_SEASON+c.COLS_PIT_ADV]
            stat_dict["career_pit_adv"] = df
            pitching["career_advanced"] = df

        elif st == "career" and sg == "fielding":
            data = []
            for s in splits:
                stats = s.get("stat")
                
                team = s.get("team",{})
                league = s.get("league",{})
                game_type = s.get("gameType")

                tm_mlbam = team.get("id","")
                tm_name = team.get("name","")
                lg_mlbam = league.get("id","")
                lg_name = league.get("name","")

                stats["game_type"] = game_type
                stats["position"] = s.get("position",{}).get("abbreviation")
                stats["tm_mlbam"] = tm_mlbam
                stats["tm_name"] = tm_name
                stats["lg_mlbam"] = lg_mlbam
                stats["lg_name"] = lg_name

                data.append(pd.Series(stats))
            df = pd.DataFrame(data=data).rename(columns=c.STATDICT)#[c.WO_SEASON+c.COLS_FLD]
            stat_dict["career_fld"] = df
            fielding["career"] = df

        elif st == "yearByYear" and sg == "hitting":
            data = []
            for s in splits:
                stats = s.get("stat")
                
                team = s.get("team",{})
                league = s.get("league",{})
                game_type = s.get("gameType")

                tm_mlbam = team.get("id","")
                tm_name = team.get("name","")
                lg_mlbam = league.get("id","")
                lg_name = league.get("name","")

                stats["season"] = s.get("season","")
                stats["game_type"] = game_type
                stats["tm_mlbam"] = tm_mlbam
                stats["tm_name"] = tm_name
                stats["lg_mlbam"] = lg_mlbam
                stats["lg_name"] = lg_name

                data.append(pd.Series(stats))
            df = pd.DataFrame(data=data).rename(columns=c.STATDICT)#[c.W_SEASON+c.COLS_HIT].sort_values(by="season",ascending=False)
            stat_dict["yby_hit"] = df
            hitting["yby"] = df

        elif st == "yearByYearAdvanced" and sg == "hitting":
            data = []
            for s in splits:
                stats = s.get("stat")
                
                team = s.get("team",{})
                league = s.get("league",{})
                game_type = s.get("gameType")

                tm_mlbam = team.get("id","")
                tm_name = team.get("name","")
                lg_mlbam = league.get("id","")
                lg_name = league.get("name","")
                
                stats["season"] = s.get("season","")
                stats["game_type"] = game_type
                stats["tm_mlbam"] = tm_mlbam
                stats["tm_name"] = tm_name
                stats["lg_mlbam"] = lg_mlbam
                stats["lg_name"] = lg_name

                data.append(pd.Series(stats))
            df = pd.DataFrame(data=data).rename(columns=c.STATDICT)#[c.W_SEASON+c.COLS_HIT_ADV].sort_values(by="season",ascending=False)
            stat_dict["yby_hit_adv"] = df
            hitting["yby_advanced"] = df

        elif st == "yearByYear" and sg == "pitching":
            data = []
            for s in splits:
                stats = s.get("stat")
                
                team = s.get("team",{})
                league = s.get("league",{})
                game_type = s.get("gameType")

                tm_mlbam = team.get("id","")
                tm_name = team.get("name","")
                lg_mlbam = league.get("id","")
                lg_name = league.get("name","")

                stats["season"] = s.get("season","")
                stats["game_type"] = game_type
                stats["tm_mlbam"] = tm_mlbam
                stats["tm_name"] = tm_name
                stats["lg_mlbam"] = lg_mlbam
                stats["lg_name"] = lg_name

                data.append(pd.Series(stats))
            df = pd.DataFrame(data=data).rename(columns=c.STATDICT)#[c.W_SEASON+c.COLS_PIT].sort_values(by="season",ascending=False)
            stat_dict["yby_pit"] = df
            pitching["yby"] = df

        elif st == "yearByYearAdvanced" and sg == "pitching":
            data = []
            for s in splits:
                stats = s.get("stat")
                
                team = s.get("team",{})
                league = s.get("league",{})
                game_type = s.get("gameType")

                tm_mlbam = team.get("id","")
                tm_name = team.get("name","")
                lg_mlbam = league.get("id","")
                lg_name = league.get("name","")
                
                stats["season"] = s.get("season","")
                stats["game_type"] = game_type
                stats["tm_mlbam"] = tm_mlbam
                stats["tm_name"] = tm_name
                stats["lg_mlbam"] = lg_mlbam
                stats["lg_name"] = lg_name

                data.append(pd.Series(stats))
            df = pd.DataFrame(data=data).rename(columns=c.STATDICT)#[c.W_SEASON+c.COLS_PIT_ADV].sort_values(by="season",ascending=False)
            stat_dict["yby_pit_adv"] = df
            pitching["yby_advanced"] = df

        elif st == "yearByYear" and sg == "fielding":
            data = []
            for s in splits:
                stats = s.get("stat")
                
                team = s.get("team",{})
                league = s.get("league",{})
                game_type = s.get("gameType")

                tm_mlbam = team.get("id","")
                tm_name = team.get("name","")
                lg_mlbam = league.get("id","")
                lg_name = league.get("name","")

                stats["season"] = s.get("season","")
                stats["game_type"] = game_type
                stats["position"] = s.get("position",{}).get("abbreviation")
                stats["tm_mlbam"] = tm_mlbam
                stats["tm_name"] = tm_name
                stats["lg_mlbam"] = lg_mlbam
                stats["lg_name"] = lg_name

                data.append(pd.Series(stats))
            df = pd.DataFrame(data=data).rename(columns=c.STATDICT)#[c.W_SEASON+c.COLS_FLD].sort_values(by="season",ascending=False)
            stat_dict["yby_fld"] = df
            fielding["yby"] = df

    for idx,df_name in enumerate(stat_dict.keys()):
        cols = df_keys_dict[str(idx)]
        df = stat_dict[df_name]
        for col in cols[:]:
            if col not in df.columns:
                cols.remove(col)
        if int(idx) >= 5 and len(df)>0:
            stat_dict[df_name] = df[cols].sort_values(by="season",ascending=False)
        else:
            stat_dict[df_name] = df[cols]#.sort_values(by="Season",ascending=False)
    
    hitting["career"]           = stat_dict["career_hit"]
    hitting["career_advanced"]  = stat_dict["career_hit_adv"]
    hitting["yby"]              = stat_dict["yby_hit"]
    hitting["yby_advanced"]     = stat_dict["yby_hit_adv"]
    pitching["career"]          = stat_dict["career_pit"]
    pitching["career_advanced"] = stat_dict["career_pit_adv"]
    pitching["yby"]             = stat_dict["yby_pit"]
    pitching["yby_advanced"]    = stat_dict["yby_pit_adv"]
    fielding["career"]          = stat_dict["career_fld"]
    fielding["yby"]             = stat_dict["yby_fld"]

    _player_stats = {
        "hitting":hitting,
        "pitching":pitching,
        "fielding":fielding
    }

    # Parsing 'roster_entries'
    #   - Also creating a seperate dataframe of previous teams' data
    past_teams_cols = ['mlbam','full','season','location','franchise','mascot','club','short','lg_mlbam','lg_name_full','lg_name_short','lg_abbrv','div_mlbam','div_name_full','div_name_short','div_abbrv','venue_mlbam','venue_name']
    past_teams_data = []
    roster_cols = ["jersey","position","status","team","tm_mlbam","from_date","to_date","status_date","forty_man","active"]
    roster_data = []
    for entry in roster_entries:
        tm = entry.get("team",{})
        lg_mlbam  = tm.get('league',{}).get('id',0)
        lg_row    = lg_df.loc[lg_mlbam]
        div_mlbam  = tm.get('league',{}).get('id',0)
        div_row    = lg_df.loc[div_mlbam]
        venue      = tm.get('venue',{})
        roster_data.append([
            entry.get("jerseyNumber","-"),
            entry.get("position",{}).get("abbreviation",""),
            entry.get("status",{}).get("description",{}),
            tm.get("name","-"),
            tm.get("id","-"),
            entry.get("startDate","-"),
            entry.get("endDate","-"),
            entry.get("statusDate","-"),
            entry.get("isActiveFortyMan",False),
            entry.get("isActive",False)
            ])
        past_teams_data.append([
            tm.get('id'),
            tm.get('name'),
            tm.get('season'),
            tm.get('locationName'),
            tm.get('franchiseName'),
            tm.get('teamName'),
            tm.get('clubName'),
            tm.get('shortName'),
            lg_mlbam,
            lg_row['name_full'],
            lg_row['name_short'],
            lg_row['abbreviation'],
            div_mlbam,
            div_row['name_full'],
            div_row['name_short'],
            div_row['abbreviation'],
            venue.get('id',0),
            venue.get('name','-')

        ])
    _player_roster_entries = pd.DataFrame(data=roster_data,columns=roster_cols)
    _player_past_teams     = pd.DataFrame(data=past_teams_data,columns=past_teams_cols)

    # Parsing 'education'
    _edu_data = []
    for e in education.get('highschools',[{}]):
        if e.get('name') is None:
            pass
        else:
            _edu_data.append([
                'highschool',
                e.get('name',''),
                e.get('city',''),
                e.get('state','')
            ])

    for e in education.get('colleges',[{}]):
        if e.get('name') is None:
            pass
        else:
            _edu_data.append([
                'college',
                e.get('name',''),
                e.get('city',''),
                e.get('state','')
            ])
    
    education = pd.DataFrame(data=_edu_data,columns=['type','school','city','state'])
    
    # Parsing 'player_info'
    first_game = player_info.get('mlbDebutDate','-')
    last_game = player_info.get('lastPlayedDate','-')
    
    _player_info = {
        'mlbam':                int(_mlbam),
        'bbrefID':              pdf['bbrefID'],
        'primary_position':     player_info.get('primaryPosition',{}),
        'givenName':            player_info['fullFMLName'],
        'fullName':             player_info['fullName'],
        'firstName':            player_info['firstName'],
        'middleName':           player_info.get('middleName','--'),
        'lastName':             player_info['lastName'],
        'nickName':             player_info.get('nickName','--'),
        'pronunciation':        player_info.get('pronunciation',''),
        'primary_number':       player_info['primaryNumber'],
        'birthDate':            player_info.get('birthDate','-'),
        'currentAge':           player_info['currentAge'],
        'birthCity':            player_info.get('birthCity','-'),
        'birthState':           player_info.get('birthStateProvince','-'),
        'birthCountry':         player_info.get('birthCountry','-'),
        'deathDate':            player_info.get('deathDate','-'),
        'deathCity':            player_info.get('deathCity','-'),
        'deathState':           player_info.get('deathStateProvince','-'),
        'deathCountry':         player_info.get('deathCountry','-'),
        'weight':               player_info['weight'],
        'height':               player_info['height'],
        'bats':                 player_info['batSide']['code'],
        'throws':               player_info['pitchHand']['code'],
        'zoneTop':              player_info['strikeZoneTop'],
        'zoneBot':              player_info['strikeZoneBottom'],
        'is_active':            player_info['active'],
        'education':            education,
        'roster_entries':       _player_roster_entries,
        'team_mlbam':           currentTeam.get('id'),
        'team_name':            currentTeam.get('name'),
        'draft':                draft,
        'first_game':           first_game,
        'last_game':            last_game,
        'debut_data':           player_info.get('debut_data').get('stats',[])
    }

    if first_game != '-':
        _player_info['first_year'] = first_game[:4]
    else:
        _player_info['first_year'] = '-'
    if last_game != '-':
        _player_info['last_year'] = last_game[:4]
    else:
        _player_info['last_year'] = '-'

    # Parsing 'player_awards'
    award_data = []
    try:
        for a in player_awards:
            award_id        = a.get('id','-')
            award_name      = a.get('name','-')
            award_date      = a.get('date','-')
            award_season    = a.get('season','-')
            award_tm_mlbam  = a.get('team',{}).get('id')
            award_tm_name   = a.get('team',{}).get('teamName')
            
            row = [award_id,award_name,award_date,award_season,award_tm_mlbam,award_tm_name]
            
            award_data.append(row)

        _player_awards = pd.DataFrame(data=award_data,columns=('award_id','award','date','season','tm_mlbam','tm_name'))
    except:
        _player_awards = pd.DataFrame()
    
    # Parsing 'transactions'
    try:
        trx_columns = ('name','mlbam','tr_type','tr','description','date','e_date','r_date','fr','fr_mlbam','to','to_mlbam')
        trx_data = []
        for t in player_transactions:
            person = t.get('person',{})
            p_name = person.get('fullName','')
            p_mlbam = person.get('id','')
            typeCode = t.get('typeCode','')
            typeTr = t.get('typeDesc')
            desc = t.get('description')

            if "fromTeam" in t.keys():
                fr = t.get("fromTeam")
                fromTeam = fr.get("name","")
                fromTeam_mlbam = fr.get("id","")
            else:
                fromTeam = "-"
                fromTeam_mlbam = ""

            if "toTeam" in t.keys():
                to = t.get("toTeam")
                toTeam = to.get("name","")
                toTeam_mlbam = to.get("id","")
            else:
                toTeam = "-"
                toTeam_mlbam = ""

            date = t.get("date","--")
            eDate = t.get("effectiveDate","--")
            rDate = t.get("resolutionDate","--")

            row = [p_name,p_mlbam,typeCode,typeTr,desc,date,eDate,rDate,fromTeam,fromTeam_mlbam,toTeam,toTeam_mlbam]
            
            trx_data.append(row)

        _player_transactions = pd.DataFrame(data=trx_data,columns=trx_columns)
    except:
        _player_transactions = pd.DataFrame()


    # on teams (mainly used for the web app)
    if _player_info['primary_position'] == "P":
        _df = _player_stats['pitching']['yby']
    else:
        _df = _player_stats['hitting']['yby']
    
    _teams = {}
    all_tm_mlbams = list(set(_df['tm_mlbam']))
    for tm_id in all_tm_mlbams:
        try:
            tm = tdf[tdf['mlbam']==int(tm_id)].iloc[0]
            _teams[str(tm_id)] = {
                "full":tm['fullName'],
                "location":tm["locationName"],
                "club":tm["clubName"],
                "slug":f'{tm["clubName"].lower().replace(" ","-")}-{tm["mlbam"]}',
            }
        except:
            pass

    fetched_data = {
        "bio":_player_bio,
        "info":_player_info,
        "stats":_player_stats,
        "awards":_player_awards,
        "transactions":_player_transactions,
        "teams":_teams,
        "past_teams":_player_past_teams
    }

    return fetched_data

def _parse_franchise_standings(data:dict,lgs_df:pd.DataFrame) -> list[pd.DataFrame,pd.DataFrame]:
    records_data = []
    splits_data  = []
    for ssn in data:
        d = ssn['teams'][0]
        rec = d['record']
        
        season    = d['season']
        mlbam     = d['id']
        name_full = d['name']
        abbrv     = d['abbreviation']
        
        div = d.get('division')
        lg_mlbam = d.get('league',{}).get('id',0)
        if div is None:
            div_mlbam    = d.get('division',{}).get('id',0)
            lg_div_mlbam = lg_mlbam
            lg_div_short = lgs_df.loc[lg_div_mlbam]['abbreviation']
        else:
            div_mlbam    = d.get('division',{}).get('id',0)
            lg_div_mlbam = div_mlbam
            lg_div_short = lgs_df.loc[lg_div_mlbam]['name_short']
        
        games_played = rec.get('gamesPlayed','')
        wins     = rec.get('wins','')
        losses   = rec.get('losses','')
        win_perc = rec.get('winningPercentage','')
        runs     = rec.get('runsScored','')
        runs_allowed = rec.get('runsAllowed','')
        run_diff = rec.get('runDifferential','')
        
        wc_gb  = rec.get('wildCardGamesBack','-')
        div_gb = rec.get('divisionGamesBack','-')
        lg_gb  = rec.get('leagueGamesBack','-')
        sp_gb  = rec.get('sportGamesBack','-')
        gb     = rec.get('gamesBack','-')
        
        lg_row    = lgs_df.loc[lg_mlbam]
        lg_abbrv  = lg_row['abbreviation']
        div_row   = lgs_df.loc[div_mlbam]
        div_short = div_row['div_part']
        
        splits_dict = {'home':'-','away':'-',
                       'left':'-','right':'-',
                       'lastTen':'-','extraInning':'-',
                       'oneRun':'-','winners':'-',
                       'day':'-','night':'-',
                       'grass':'-','turf':'-',
                       'American League':'-','National League':'-',
                       'east':'-','central':'-','west':'-',
                       }
        
        records = rec.get('records',{})
        record_splits = records.get('splitRecords',[{}])
        record_lgs    = records.get('leagueRecords',[{}])
        record_divs   = records.get('divisionRecords',[{}])

        for lg_rec in record_lgs:
            lg = lg_rec.get('league',{}).get('name','')
            lg_wins   = lg_rec.get('wins','')
            lg_losses = lg_rec.get('losses','')
            splits_dict[lg] = f'{lg_wins}-{lg_losses}'
            
        for lg_rec in record_divs:
            lg = lg_rec.get('division',{}).get('name','')
            
            if 'east' in lg.lower():
                lg = 'east'
            elif 'central' in lg.lower():
                lg = 'central'
            elif 'west' in lg.lower():
                lg = 'west'
                
            lg_wins   = lg_rec.get('wins','')
            lg_losses = lg_rec.get('losses','')
            splits_dict[lg] = f'{lg_wins}-{lg_losses}'
        
        for s in record_splits:
            rec_type = s.get('type')
            if rec_type in splits_dict.keys():
                rec_wins   = s['wins']
                rec_losses = s['losses']
                splits_dict[rec_type] = f'{rec_wins}-{rec_losses}'
        
        records_data.append([season,
                             mlbam,
                             abbrv,
                             name_full,
                             lg_div_short,
                             games_played,
                             wins,
                             losses,
                             win_perc,
                             runs,
                             runs_allowed,
                             run_diff,
                             gb,
                             wc_gb,
                             div_gb,
                             lg_gb,
                             sp_gb])
        
        splits_data.append([season,
                            mlbam,
                            name_full,
                            lg_mlbam,
                            lg_abbrv,
                            div_mlbam,
                            div_short,
                            games_played,
                            wins,
                            losses,
                            win_perc,
                            runs,
                            runs_allowed,
                            run_diff,
                            splits_dict['American League'],
                            splits_dict['National League'],
                            splits_dict['east'],
                            splits_dict['central'],
                            splits_dict['west'],
                            splits_dict['home'],
                            splits_dict['away'],
                            splits_dict['right'],
                            splits_dict['left'],
                            splits_dict['lastTen'],
                            splits_dict['extraInning'],
                            splits_dict['oneRun'],
                            splits_dict['winners'],
                            splits_dict['day'],
                            splits_dict['night'],
                            splits_dict['grass'],
                            splits_dict['turf'],
                            ])
        
    records_df = pd.DataFrame(data=records_data,columns=c.YBY_REC_COLS)
    splits_df  = pd.DataFrame(data=splits_data, columns=c.YBY_REC_SPLIT_COLS)
    records_df.sort_values(by='season',ascending=False,inplace=True)
    splits_df.sort_values(by='season',ascending=False,inplace=True)
    
    return records_df, splits_df
        
def _franchise_data(mlbam,**kwargs) -> dict:
    """Fetch various team season data & information for a team in one API call

    Parameters
    ----------
    mlbam : str or int
        Official team MLB ID

    season : int or str
        season/year ID. If season is not specified, data for the entire franchise will be retrieved by default

    rosterType : str
        specify the type of roster to retrieve (Default is "40Man")

    ***

    Keys for Franchise Data (all year-by-year data)
    ---------------------------
    "records"
    "standings"
    "hitting" 
    "hitting_advanced"
    "pitching"
    "pitching_advanced"
    "fielding"

    Keys Team Data for specific year
    -----------------------------------
    "records"
    "standings"
    "roster_hitting"
    "roster_pitching"
    "roster_fielding"

    """
    _mlbam = mlbam

    records = mlbdata.get_yby_records()
    records = records[records['tm_mlbam']==int(mlbam)]
    standings = mlbdata.get_standings_df()
    standings = standings[standings['mlbam']==int(mlbam)]

    # == ASYNC STARTS HERE ===============================================
    lgs_df = mlbdata.get_leagues_df().set_index('mlbam')
    team_df = mlbdata.mlbdata.get_teams_df()
    team_df = team_df[team_df['mlbam']==int(mlbam)]
    firstYear = team_df.iloc[0]["first_year"]
    years = range(firstYear,int(default_season())+1)

    urls = []
    for year in years:
        urls.append(f"https://statsapi.mlb.com/api/v1/teams/{mlbam}?hydrate=standings&season={year}")                   # yby_data

    hydrations = f"nextSchedule(limit=5),previousSchedule(limit=1,season={default_season()}),league,division"           # ---- (hydrations for 'team_info') ----
    urls.append(c.BASE + f"/teams/{mlbam}?hydrate={hydrations}")                                                          # team_info
    urls.append((c.BASE + f"/teams/{mlbam}/stats?stats=yearByYear,yearByYearAdvanced&group=hitting,pitching,fielding"))   # team_stats
    urls.append(f"https://statsapi.mlb.com/api/v1/teams/{mlbam}/roster/allTime")                                        # all_players
    urls.append(f"https://statsapi.mlb.com/api/v1/awards/MLBHOF/recipients")                                            # hof_players
    urls.append(f"https://statsapi.mlb.com/api/v1/awards/RETIREDUNI_{mlbam}/recipients")                                # retired_numbers

    # https://statsapi.mlb.com/api/v1/teams/stats/leaders?season=2021&leaderCategories=wins,losses
    # https://statsapi.mlb.com/api/v1/teams/145/roster/coach?season=1904

    resps = fetch(urls)
    
    yby_data = resps[:-5]
    team_info = resps[-5]
    team_stats = resps[-4]
    all_players = resps[-3]
    hof_players = resps[-2]
    retired_numbers = resps[-1]

    records_df, splits_df = _parse_franchise_standings(data=yby_data,lgs_df=lgs_df)
    # ---- Parsing 'team_info' ---------

    # Includes basic team information and recent/upcoming schedule information

    team_info_parsed = {}
    teams = team_info["teams"][0]
    lg  = teams.get("league",{})
    div = teams.get("division",{})
    team_info_parsed["mlbam"]               = teams["id"]
    team_info_parsed["full_name"]           = teams["name"]
    team_info_parsed["location_name"]       = teams["locationName"]
    team_info_parsed["franchise_name"]      = teams["franchiseName"]
    team_info_parsed["team_name"]           = teams["teamName"]
    team_info_parsed["club_name"]           = teams["clubName"]
    team_info_parsed["short_name"]          = teams["shortName"]
    team_info_parsed["venue_mlbam"]         = teams.get("venue",{}).get("id","")
    team_info_parsed["venue_name"]          = teams.get("venue",{}).get("name","")
    team_info_parsed["first_year"]          = teams["firstYearOfPlay"]
    team_info_parsed["league_mlbam"]        = lg.get("id","")
    team_info_parsed["league_name"]         = lg.get("name","")
    team_info_parsed["league_short"]        = lg.get("nameShort","")
    team_info_parsed["league_abbrv"]        = lg.get("abbreviation","")
    team_info_parsed["div_mlbam"]           = div.get("id","")
    team_info_parsed["div_name"]            = div.get("name","")
    team_info_parsed["div_short"]           = div.get("nameShort","")
    team_info_parsed["div_abbrv"]           = div.get("abbreviation","")
    team_info_parsed["season"]              = teams["season"]

    sched_df_cols = ['season','date','gamePk','game_type','away_mlbam','away_name','home_mlbam','home_name','double_header','series_game','series_length']
    sched_prev_data = []
    for d in teams.get("previousGameSchedule",{}).get("dates",[{}]):
        date_obj = dt.datetime.strptime(d["date"],r"%Y-%m-%d")
        for gm in d["games"]:
            away = gm.get("teams",{}).get("away")
            home = gm.get("teams",{}).get("home")
            sched_prev_data.append([
                gm.get("season"),
                date_obj,
                gm.get("gamePk"),
                gm.get("gameType"),
                away.get("team",{}).get("id"),
                away.get("team",{}).get("name"),
                home.get("team",{}).get("id"),
                home.get("team",{}).get("name"),
                False if gm.get("doubleHeader") == "N" else True,
                gm.get("seriesGameNumber"),
                gm.get("gamesInSeries")
            ])
    sched_prev_df = pd.DataFrame(data=sched_prev_data,columns=sched_df_cols)
    team_info_parsed["sched_prev"] = sched_prev_df

    sched_next_data = []
    for d in teams.get("nextGameSchedule",{}).get("dates",[{}]):
        date_obj = dt.datetime.strptime(d["date"],r"%Y-%m-%d")
        for gm in d["games"]:
            away = gm.get("teams",{}).get("away")
            home = gm.get("teams",{}).get("home")
            sched_next_data.append([
                gm.get("season"),
                date_obj,
                gm.get("gamePk"),
                gm.get("gameType"),
                away.get("team",{}).get("id"),
                away.get("team",{}).get("name"),
                home.get("team",{}).get("id"),
                home.get("team",{}).get("name"),
                False if gm.get("doubleHeader") == "N" else True,
                gm.get("seriesGameNumber"),
                gm.get("gamesInSeries")
            ])
    sched_next_df = pd.DataFrame(data=sched_next_data,columns=sched_df_cols)
    team_info_parsed["sched_next"] = sched_next_df

    # ---- Parsing 'team_stats' --------
    team_stats_json = team_stats # using alias for consistency

    hit_data = []
    hit_adv_data = []
    pitch_data = []
    pitch_adv_data = []
    field_data = []

    for g in team_stats_json.get("stats",[{}]):
        st = g.get("type",{}).get("displayName")
        sg = g.get("group",{}).get("displayName")
        if sg == "hitting" and st == "yearByYear":
            for s in g.get("splits",[{}]):
                stats = s.get("stat",{})
                season = s.get("season")
                stats["season"] = season
                hit_data.append(stats)

        elif sg == "hitting" and st == "yearByYearAdvanced":
            for s in g.get("splits",[{}]):
                stats = s.get("stat",{})
                season = s.get("season")
                stats["season"] = season
                hit_adv_data.append(stats)

        elif sg == "pitching" and st == "yearByYear":
            for s in g.get("splits",[{}]):
                stats = s.get("stat",{})
                season = s.get("season")
                stats["season"] = season
                pitch_data.append(stats)

        elif sg == "pitching" and st == "yearByYearAdvanced":
            for s in g.get("splits",[{}]):
                stats = s.get("stat",{})
                season = s.get("season")
                stats["season"] = season
                pitch_adv_data.append(stats)

        elif sg == "fielding" and st == "yearByYear":
            for s in g.get("splits",[{}]):
                stats = s.get("stat",{})
                season = s.get("season")
                stats["season"] = season
                field_data.append(stats)

    stat_dict = {
        'hit_df'       : pd.DataFrame(data=hit_data).rename(columns=c.STATDICT),
        'hit_adv_df'   : pd.DataFrame(data=hit_adv_data).rename(columns=c.STATDICT),
        'pitch_df'     : pd.DataFrame(data=pitch_data).rename(columns=c.STATDICT),
        'pitch_adv_df' : pd.DataFrame(data=pitch_adv_data).rename(columns=c.STATDICT),
        'field_df'     : pd.DataFrame(data=field_data).rename(columns=c.STATDICT),
    }

    # need to remove any stats that are not available at the team-level (e.g. "IR","IRS","BQ","BQS" etc.)
    df_keys_dict = {
        "0":c.COLS_HIT,
        "1":c.COLS_HIT_ADV,
        "2":c.COLS_PIT,
        "3":c.COLS_PIT_ADV,
        "4":c.COLS_FLD
    }

    for idx,df_name in enumerate(stat_dict.keys()):
        cols = df_keys_dict[str(idx)]
        df = stat_dict[df_name]
        for col in cols[:]:
            if col not in df.columns:
                cols.remove(col)
        
        stat_dict[df_name] = df[['season'] + cols].sort_values(by="season",ascending=False)

    hit_df          = stat_dict['hit_df']
    hit_adv_df      = stat_dict['hit_adv_df']
    pitch_df        = stat_dict['pitch_df']
    pitch_adv_df    = stat_dict['pitch_adv_df']
    field_df        = stat_dict['field_df']

    # ---- Parsing 'all_players' --------

    rost_cols = [
        'mlbam',
        'name',
        'jersey_number',
        'pos',
        'status',
        'status_code']
    rost_data = []

    for p in all_players["roster"]:
        person          = p.get("person")
        mlbam           = person.get("id")
        name            = person.get("fullName")
        jersey_number   = p.get("jerseyNumber")
        position        = p.get("position")
        pos             = position.get("abbreviation")
        status          = p.get("status",{}).get("description")
        status_code     = p.get("status",{}).get("code")
        
        rost_data.append([
            mlbam,
            name,
            jersey_number,
            pos,
            status,
            status_code
        ])
    all_players_df = pd.DataFrame(data=rost_data,columns=rost_cols).sort_values(by="name")


    # ---- Parsing 'hof_players' --------
    hof_data = []

    for a in hof_players["awards"]:
        if str(a.get("team",{}).get("id")) == str(_mlbam):
            p = a.get("player")
            hof_data.append([
                a.get("season"),
                a.get("date"),
                p.get("id"),
                p.get("nameFirstLast"),
                p.get("primaryPosition",{}).get("abbreviation"),
                a.get("votes",""),
                a.get("notes","")
            ])
    hof_df = pd.DataFrame(data=hof_data,columns=['season','date','mlbam','name','pos','votes','notes'])

    # ---- Parsing 'retired_numbers' ----
    retired_numbers_data = []

    for a in retired_numbers["awards"]:
        player = a.get("player",{})
        retired_numbers_data.append([
            a.get("season"),
            a.get("date"),
            a.get("notes"),     # this is the "retired number" value
            player.get("id"),
            player.get("nameFirstLast"),
            player.get("primaryPosition",{}).get("abbreviation",""),
        ])

    retired_numbers_df = pd.DataFrame(data=retired_numbers_data,columns=['season','date','number','mlbam','name','pos'])

    fetched_data = {
        "record_splits":records.reset_index(drop=True),
        "records":standings.reset_index(drop=True),
        "yby_data":yby_data,
        "team_info":team_info_parsed,
        "hitting":hit_df.reset_index(drop=True),
        "hitting_advanced":hit_adv_df.reset_index(drop=True),
        "pitching":pitch_df.reset_index(drop=True),
        "pitching_advanced":pitch_adv_df.reset_index(drop=True),
        "fielding":field_df.reset_index(drop=True),
        "all_players":all_players_df,
        "hof":hof_df,
        "retired_numbers":retired_numbers_df,
        "temp":hof_players,
        "records_df":records_df,
        "splits_df":splits_df
    }

    return fetched_data

# ===============================================================
# PLAYER Functions
# ===============================================================

def player_stats(mlbam,**kwargs):
    """Get various types of player stats, game logs, and pitch logs

    Parameters
    ----------
    mlbam : str or int, required
        player's official MLB ID

    statGroup : str or list, required
        the stat group(s) for which to receive stats. (e.g. "hitting", "pitching", "fielding")

    statType : str or list, required
        the type of stats to search for (e.g. "season", "vsPlayer","yearByYearAdvanced", etc...)

    season : str or int, optional (Default is the current season; or the last completed if in off-season)
        the season to search for results (some cases may allow a comma-delimited list of values)

    gameType : str, optional
        filter results by game type (e.g. "R", "S", "D,L,W", etc.)

    opposingTeamId : str or int, conditionally required
        the opposing team ID to filter results for statTypes "vsTeam", "vsTeamTotal", "vsTeam5Y"

    opposingPlayerId : str or int, conditionally required
        the opposing player ID to filter results for statTypes "vsPlayer", "vsPlayerTotal", "vsPlayer5Y"

    date : str or datetime, optional
        date to search for results (str format -> YYYY-mm-dd)

    startDate : str or datetime, conditionally required
        the starting date boundary to search for results (str format -> YYYY-mm-dd)
        Required for following stat types 'byDateRange', 'byDateRangeAdvanced'
    
    endDate : str or datetime, conditionally required
        the ending date boundary to search for results (str format -> YYYY-mm-dd)
        Required for following stat types 'byDateRange', 'byDateRangeAdvanced'

    eventType : str, optional
        filter results by event type
    
    pitchType : str,optional
        filter results by the type of pitch thrown (for stat types 'pitchLog' and 'playLog')

    oppTeamId : str, optional
        Alias for 'opposingTeamId'
    
    oppPlayerId : str, optional
        Alias for 'opposingPlayerId'

    
    Returns
    -------
    pandas.DataFrame

    See Also
    --------
    mlb.player_hitting()

    mlb.team_stats()

    mlb.statTypes()

    mlb.eventTypes()

    mlb.gameTypes()

    """
    kwargs = ExtendedDict(kwargs)
    params = {
        'group':'hitting,pitching,fielding',
        'stats':'yearByYear,yearByYearAdvanced',
        'hydrate':'person'
    }
    statType = kwargs.dget('stat_type','statType','types','type')
    if statType is not None:
        if 'season' in statType:
            if kwargs.get('season') is None:
                params['season'] = default_season()
            else:
                params['season'] = kwargs['season']
        params['stats'] = statType
    
    statGroup = kwargs.dget('statGroup','stat_group','groups','group')
    if statGroup is not None:
        params['group'] = statGroup
        
    url = c.BASE + f"/people/{mlbam}/stats"
    resp = requests.get(url,params=params)
    
    dfs = {}
    
    for s in resp.json().get('stats',[{}]):
        if 'advanced' not in s.get('type',{}).get('displayName','').lower():
            if s.get('group',{}).get('displayName') == "hitting":
                dfs['hitting'] = parsing._parse_player_stats(s.get('splits',[{}]))
            elif s.get('group',{}).get('displayName') == "pitching":
                dfs['pitching'] = parsing._parse_player_stats(s.get('splits',[{}]))
            elif s.get('group',{}).get('displayName') == "fielding":
                dfs['fielding'] = parsing._parse_player_stats(s.get('splits',[{}]))
        else:
            if s.get('group',{}).get('displayName') == "hitting":
                dfs['hitting_adv'] = parsing._parse_player_stats(s.get('splits',[{}]))
            elif s.get('group',{}).get('displayName') == "pitching":
                dfs['pitching_adv'] = parsing._parse_player_stats(s.get('splits',[{}]))
    
    return dclass.StatTypeCollection(
        dfs['hitting'],dfs['pitching'],dfs['fielding'],
        dfs['hitting_adv'],dfs['pitching_adv']
    )
    
def player_game_logs(mlbam,season=None,statGroup=None,gameType=None,**kwargs) -> pd.DataFrame:
    """Get a player's game log stats for a specific season

    Parameters
    ----------
    mlbam : str or int
        player's official MLB ID
    
    seasons : str or int, optional (Default is the most recent season)
        filter by season

    statGroup : str, optional
        filter results by stat group ('hitting','pitching','fielding')

    gameType : str, optional
        filter results by game type

    startDate : str, optional
        include games AFTER a specified date
    
    endDate : str, optional
        include games BEFORE a specified date

    """

    params = {
        "stats":"gameLog"
    }
    if kwargs.get("seasons") is not None:
        season = kwargs["seasons"]

    if kwargs.get("group") is not None:
        statGroup = kwargs["group"]
    elif kwargs.get("groups") is not None:
        statGroup = kwargs["groups"]
    elif kwargs.get("statGroups") is not None:
        statGroup = kwargs["statGroups"]
    else:
        statGroup = statGroup

    if kwargs.get("gameTypes") is not None:
        gameType = kwargs["gameTypes"]
    elif kwargs.get("game_type") is not None:
        gameType = kwargs["game_type"]

    if season is not None:
        params["season"] = season
    if statGroup is not None:
        params["group"] = statGroup
    if gameType is not None:
        params["gameType"] = gameType
    if kwargs.get("startDate") is not None:
        params["startdate"] = kwargs["startDate"]
    if kwargs.get("endDate") is not None:
        params["endDate"] = kwargs["endDate"]

    url = c.BASE + f"/people/{mlbam}/stats?"
    resp = requests.get(url,params=params)

    data = []

    # "sg" referes to each stat group in the results
    for sg in resp.json()["stats"]:
        if sg["group"]["displayName"] == "hitting":
            # "g" refers to each game
            for g in sg.get("splits"):
                game = g.get("stat",{})

                positions = []
                player = g.get("player",{})
                mlbam = player.get("id")
                name = player.get("name")
                for pos in g.get("positionsPlayed",[]):
                    positions.append(pos.get("abbreviation"))
                positions = "|".join(positions)
                
                tm = g.get("team",{})
                tm_opp_mlbam = g.get("opponent",{}).get("id")
                tm_opp_name = g.get("opponent",{}).get("name")
                
                game["date"] = g.get("date","")
                game["isHome"] = g.get("isHome",False)
                game["isWin"] = g.get("isWin",False)
                game["gamePk"] = g.get("game",{}).get("gamePk")
                game["mlbam"] = mlbam
                game["name"] = name
                game["positions"] = positions
                game["tm_mlbam"] = tm.get("id")
                game["name"] = tm.get("name")
                game["opp_mlbam"] = tm_opp_mlbam
                game["opp_name"] = tm_opp_name

                data.append(pd.Series(game))

            break
    

    columns = ['date','isHome','isWin','gamePk','mlbam','name','positions','tm_mlbam','opp_mlbam','opp_name','G','GO','AO','R','2B','3B','HR','SO','BB','IBB','H','HBP','AVG','AB','OBP','SLG','OPS','CS','SB','SB%','GIDP','GITP','P','PA','TB','RBI','LOB','sB','sF','BABIP','GO/AO','CI','AB/HR']
    # df = pd.DataFrame(data=data).rename(columns=c.STATDICT)[columns]
    df = pd.DataFrame(data=data).rename(columns=c.STATDICT)#[columns]

    return df

def player_date_range(mlbam,statGroup,startDate,endDate,gameType=None) -> pd.DataFrame:
    """Get a player's stats for a specified date range

    Parameters
    ----------
    mlbam : str or int, required
        player's official MLB ID
    
    startDate : str, required
        include games AFTER a specified date (format: "YYYY-mm-dd")
    
    endDate : str, required
        include games BEFORE a specified date (format: "YYYY-mm-dd")

    statGroup : str, required
        filter results by stat group ('hitting','pitching','fielding')

    gameType : str, optional
        filter results by game type (only one gameType can be specified per call)

    """

    mlbam = str(mlbam)

    params = {
        "stats":"byDateRange",
        "group":statGroup,
        "startDate":startDate,
        "endDate":endDate
    }

    if gameType is not None:
        params["gameType"] = gameType

    url = c.BASE + f"/people/{mlbam}/stats?"

    resp = requests.get(url,params=params)
    resp_json = resp.json()

    data = []

    for s in resp_json["stats"][0]["splits"]:
        if s.get("sport",{}).get("id") == 1:
            stats = s.get("stat")
            stats["tm_mlbam"] = s.get("team",{}).get("id")
            stats["tm_name"] = s.get("team",{}).get("name")
            data.append(pd.Series(stats))
    
    columns = ['tm_mlbam','team','G','GO','AO','R','2B','3B','HR','SO','BB','IBB','H','HBP','AVG','AB','OBP','SLG','OPS','CS','SB','SB%','GIDP','GITP','P','PA','TB','RBI','LOB','sB','sF','BABIP','GO/AO','CI','AB/HR']
    df = pd.DataFrame(data).rename(columns=c.STATDICT)

    return df

def player_date_range_advanced(mlbam,statGroup,startDate,endDate,gameType=None) -> pd.DataFrame:
    """Get a player's stats for a specified date range

    Parameters
    ----------
    mlbam : str or int, required
        player's official MLB ID
    
    startDate : str, required
        include games AFTER a specified date (format: "YYYY-mm-dd")
    
    endDate : str, required
        include games BEFORE a specified date (format: "YYYY-mm-dd")

    statGroup : str, required
        filter results by stat group ('hitting','pitching','fielding')

    gameType : str, optional
        filter results by game type (only one gameType can be specified per call)

    """

    mlbam = str(mlbam)

    params = {
        "stats":"byDateRangeAdvanced",
        "group":statGroup,
        "startDate":startDate,
        "endDate":endDate
    }

    if gameType is not None:
        params["gameType"] = gameType

    url = c.BASE + f"/people/{mlbam}/stats?"

    resp = requests.get(url,params=params)
    resp_json = resp.json()

    data = []

    for s in resp_json["stats"][0]["splits"]:
        if s.get("sport",{}).get("id") == 1:
            stats = s.get("stat")
            stats["tm_mlbam"] = s.get("team",{}).get("id")
            stats["tm_name"] = s.get("team",{}).get("name")
            data.append(pd.Series(stats))
    
    columns = ['tm_mlbam','team','PA','TB','sB','sF','BABIP','exBH','HBP','GIDP','P','P/PA','BB/PA','SO/PA','HR/PA','BB/SO','ISO','GO']
    # df = pd.DataFrame(data).rename(columns=c.STATDICT)[columns]
    df = pd.DataFrame(data).rename(columns=c.STATDICT)

    return df

def player_splits(mlbam,statGroup,sitCodes,season=None,gameType=None) -> pd.DataFrame:
    """Get a player's stats for a specified date range

    Parameters
    ----------
    mlbam : str or int, required
        player's official MLB ID

    statGroup : str, required
        filter results by stat group ('hitting','pitching','fielding')
    
    sitCodes : str, required
        situation code(s) to get stats for ("h" for home games, "a" for away games, "n" for night games, etc.)

    gameType : str, optional
        filter results by game type (only one gameType can be specified per call)

    """

    mlbam = str(mlbam)

    params = {
        "stats":"statSplits",
        "group":statGroup,
        "sitCodes":sitCodes,
    }

    if gameType is not None:
        params["gameType"] = gameType
    if season is not None:
        params["season"] = season
    else:
        params["season"] = default_season()

    url = c.BASE + f"/people/{mlbam}/stats?"

    resp = requests.get(url,params=params)
    resp_json = resp.json()

    data = []

    for s in resp_json["stats"][0]["splits"]:
        if s.get("sport",{}).get("id") == 1:
            stats = s.get("stat")
            stats["tm_mlbam"] = s.get("team",{}).get("id")
            stats["tm_name"] = s.get("team",{}).get("name")
            stats["season"] = s.get("season")
            stats["split_code"] = s.get("split",{}).get("code")
            stats["split"] = s.get("split",{}).get("description")
            data.append(pd.Series(stats))
    
    columns = ['season','split_code','split','tm_mlbam','team','G','GO','AO','R','2B','3B','HR','SO','BB','IBB','H','HBP','AVG','AB','OBP','SLG','OPS','CS','SB','SB%','GIDP','GITP','P','PA','TB','RBI','LOB','sB','sF','BABIP','GO/AO','CI','AB/HR']
    # df = pd.DataFrame(data).rename(columns=c.STATDICT)[columns]
    df = pd.DataFrame(data).rename(columns=c.STATDICT)
    return df

def player_splits_advanced(mlbam,statGroup,sitCodes,season=None,gameType=None) -> pd.DataFrame:
    """Get a player's stats for a specified date range

    Parameters
    ----------
    mlbam : str or int, required
        player's official MLB ID

    statGroup : str, required
        filter results by stat group ('hitting','pitching','fielding')
    
    sitCodes : str, required
        situation code(s) to get stats for ("h" for home games, "a" for away games, "n" for night games, etc.)

    gameType : str, optional
        filter results by game type (only one gameType can be specified per call)

    """

    mlbam = str(mlbam)

    params = {
        "stats":"statSplitsAdvanced",
        "group":statGroup,
        "sitCodes":sitCodes,
    }

    if gameType is not None:
        params["gameType"] = gameType
    if season is not None:
        params["season"] = season
    else:
        params["season"] = default_season()

    url = c.BASE + f"/people/{mlbam}/stats?"

    resp = requests.get(url,params=params)
    resp_json = resp.json()

    data = []

    for s in resp_json["stats"][0]["splits"]:
        if s.get("sport",{}).get("id") == 1:
            stats = s.get("stat")
            stats["tm_mlbam"] = s.get("team",{}).get("id")
            stats["tm_name"] = s.get("team",{}).get("name")
            stats["season"] = s.get("season")
            stats["split_code"] = s.get("split",{}).get("code")
            stats["split"] = s.get("split",{}).get("description")
            data.append(pd.Series(stats))
    
    columns = ['season','split_code','split','tm_mlbam','team','PA','TB','sB','sF','BABIP','exBH','HBP','GIDP','P','P/PA','BB/PA','SO/PA','HR/PA','BB/SO','ISO']
    # df = pd.DataFrame(data).rename(columns=c.STATDICT)[columns]
    df = pd.DataFrame(data).rename(columns=c.STATDICT)#[columns]
    return df

# ===============================================================
# TEAM Functions
# ===============================================================

def team_stats(mlbam,**kwargs) -> pd.DataFrame:
    kwargs = ExtendedDict(kwargs)
    params = {
        'group':'hitting,pitching,fielding',
        'stats':'yearByYear,yearByYearAdvanced'
    }
    statType = kwargs.dget('statType','stat_type','types','type')
    if statType is not None:
        if 'season' in statType:
            if kwargs.get('season') is None:
                params['season'] = default_season()
            else:
                params['season'] = kwargs['season']
        params['stats'] = statType
    
    statGroup = kwargs.dget('statGroup','stat_group','groups','group')
    if statGroup is not None:
        params['group'] = statGroup
    
    url = c.BASE + f"/teams/{mlbam}/stats"
    resp = requests.get(url,params=params)
    
    dfs = {}
    
    for s in resp.json().get('stats',[{}]):
        if s.get('type',{}).get('displayName').lower().find('advanced') == -1:
            if s.get('group',{}).get('displayName') == "hitting":
                dfs['hitting'] = parsing._parse_team_stats(s.get('splits',[{}]),True)
            elif s.get('group',{}).get('displayName') == "pitching":
                dfs['pitching'] = parsing._parse_team_stats(s.get('splits',[{}]),True)
            elif s.get('group',{}).get('displayName') == "fielding":
                dfs['fielding'] = parsing._parse_team_stats(s.get('splits',[{}]),True)
        else:
            if s.get('group',{}).get('displayName') == "hitting":
                dfs['hitting_adv'] = parsing._parse_team_stats(s.get('splits',[{}]),True)
            elif s.get('group',{}).get('displayName') == "pitching":
                dfs['pitching_adv'] = parsing._parse_team_stats(s.get('splits',[{}]),True)
    
    return dclass.StatTypeCollection(
        dfs['hitting'],dfs['pitching'],dfs['fielding'],
        dfs['hitting_adv'],dfs['pitching_adv']
    )
    # return StatTypeCollection(**dfs)

def team_game_logs(mlbam,season=None,statGroup=None,gameType=None,**kwargs) -> pd.DataFrame:
    """Get a team's game log stats for a specific season

    Parameters
    ----------
    mlbam : str or int
        team's official MLB ID
    
    seasons : str or int, optional (Default is the most recent season)
        filter by season

    statGroup : str, optional
        filter results by stat group ('hitting','pitching','fielding')

    gameType : str, optional
        filter results by game type (only one can be specified per call)

    startDate : str, optional
        include games AFTER a specified date
    
    endDate : str, optional
        include games BEFORE a specified date

    """

    params = {
        "stats":"gameLog"
    }
    if kwargs.get("seasons") is not None:
        season = kwargs["seasons"]

    if kwargs.get("group") is not None:
        statGroup = kwargs["group"]
    elif kwargs.get("groups") is not None:
        statGroup = kwargs["groups"]
    elif kwargs.get("statGroups") is not None:
        statGroup = kwargs["statGroups"]
    else:
        statGroup = statGroup

    if kwargs.get("gameTypes") is not None:
        gameType = kwargs["gameTypes"]
    elif kwargs.get("game_type") is not None:
        gameType = kwargs["game_type"]

    if season is not None:
        params["season"] = season
    if statGroup is not None:
        params["group"] = statGroup
    if gameType is not None:
        params["gameType"] = gameType
    if kwargs.get("startDate") is not None:
        params["startdate"] = kwargs["startDate"]
    if kwargs.get("endDate") is not None:
        params["endDate"] = kwargs["endDate"]

    url = c.BASE + f"/teams/{mlbam}/stats?"
    resp = requests.get(url,params=params)

    data = []
    tms_df = mlbdata.get_teams_df(year=season).set_index("mlbam")

    # "sg" referes to each stat group in the results
    for sg in resp.json()["stats"]:
        if sg["group"]["displayName"] == "hitting":
            # "g" refers to each game
            for g in sg.get("splits"):
                game = g.get("stat",{})
                
                tm = g.get("team",{})
                tm_opp_mlbam = g.get("opponent",{}).get("id")
                
                game["date"] = g.get("date","")
                game["isHome"] = g.get("isHome",False)
                game["isWin"] = g.get("isWin",False)
                game["gamePk"] = g.get("game",{}).get("gamePk")
                game["mlbam"] = tm.get("id")
                game["name"] = tm.get("name")
                game["opp_mlbam"] = tm_opp_mlbam
                game["opp_name"] = tms_df.loc[tm_opp_mlbam]["fullName"]

                data.append(pd.Series(game))

            break
    columns = ['date','isHome','isWin','gamePk','mlbam','name','opp_mlbam','opp_name','G','GO','AO','R','2B','3B','HR','SO','BB','IBB','H','HBP','AVG','AB','OBP','SLG','OPS','CS','SB','SB%','GIDP','P','PA','TB','RBI','LOB','sB','sF','BABIP','GO/AO','AB/HR']
    
    df = pd.DataFrame(data=data).rename(columns=c.STATDICT)[columns]

    return df

def team_roster(mlbam,season=None,rosterType=None,**kwargs) -> pd.DataFrame:
    """Get team rosters by season

    Parameters
    ----------
    mlbam : str or int, required
        team's official MLB ID
    
    season : str or int, optional (Default is the current season or the most recently completed if in the off-season)
        specify a team's roster by season. This value will be ignored if rosterType="allTime" or rosterType="depthChart".

    rosterType : str, optional
        specify the type of roster to retrieve

    Roster Types
    ------------
    - "active" (Default)
        - Active roster for a team
    - "40Man"
        - 40-man roster for a team
    - "depthChart"
        - Depth chart for a team
    - "fullSeason"
        - Full roster including active and inactive players for a season
    - "fullRoster"
        - Full roster including active and inactive players
    - "allTime"
        - All Time roster for a team
    - "coach"
        - Coach roster for a team
    - "gameday" (NOT WORKING)
        - Roster for day of game
    - "nonRosterInvitees"
        - Non-Roster Invitees

    """

    params = {}
    if season is not None:
        params["season"] = season
    elif season is None:
        params["season"] = default_season()
    if rosterType is not None:
        params["rosterType"] = rosterType
    if len(kwargs) != 0:
        for k,v in kwargs.items():
            params["k"] = v
    if kwargs.get("hydrate") is not None:
        params["hydrate"] = kwargs["hydrate"]

    # hydrate=person(rosterEntries)
    url = c.BASE + f"/teams/{mlbam}/roster"

    resp = requests.get(url,params=params)
    roster = resp.json()["roster"]
    
    columns = [
        'mlbam',
        'name',
        'name_first',
        'name_last',
        'name_lastfirst',
        'jersey_number',
        'pos',
        'status',
        'status_code'
        ]
    data = []

    for p in roster:
        person          = p.get("person")
        mlbam           = person.get("id")
        name            = person.get("fullName")
        name_first      = person.get("firstName")
        name_last       = person.get("lastName")
        name_lastfirst  = person.get("lastFirstName")
        jersey_number   = p.get("jerseyNumber")
        position        = p.get("position")
        pos             = position.get("abbreviation")
        status          = p.get("status",{}).get("description")
        status_code     = p.get("status",{}).get("code")
        
        data.append([
            mlbam,
            name,
            name_first,
            name_last,
            name_lastfirst,
            jersey_number,
            pos,
            status,
            status_code
        ])
    df = pd.DataFrame(data=data,columns=columns).sort_values(by="name")

    return df

def team_appearances(mlbam):
    gt_types = {'F':'wild_card_series','D':'division_series','L':'league_series','W':'world_series','P':'playoffs'}
    sort_orders = {'F':1,'D':2,'L':3,'W':4}
    with requests.session() as sesh:
        data = []
        for gt in ('F','D','L','W'):
            url = f"https://statsapi.mlb.com/api/v1/teams/{mlbam}/stats?stats=yearByYearPlayoffs&group=pitching&gameType={gt}&fields=stats,splits,stat,wins,losses,season"
            resp = sesh.get(url)
            game_type = gt_types[gt]
            years = resp.json()["stats"][0]["splits"]
            for y in years:
                season = y.get("season","")
                wins = y.get("stat",{}).get("wins",0)
                losses = y.get("stat",{}).get("losses",0)
                if wins > losses:
                    title_winner = True
                else:
                    title_winner = False
                sort_order = sort_orders[gt]
                
                data.append([
                    season,gt,game_type,wins,losses,title_winner,sort_order
                ])
                

                
        df = pd.DataFrame(data=data,columns=['season','gt','game_type','wins','losses','title_winner','sort_order']).sort_values(by=["season","sort_order"],ascending=[True,True]).reset_index(drop=True)
        
        return df

# ===============================================================
# LEAGUE Functions
# ===============================================================

def league_stats(league="all",season=None,hydrate:Optional[str]=None,**kwargs) -> dclass.StatTypeCollection:
    params = {
        'group':'hitting,pitching,fielding',
        'stats':'season,seasonAdvanced',
        'sportId':'1',
    }
    if kwargs.get('statGroup',kwargs.get('group',kwargs.get('groups'))) is not None:
        params['group'] = kwargs.get('statGroup',kwargs.get('group',kwargs.get('groups')))
    if kwargs.get('statType',kwargs.get('types',kwargs.get('type'))) is not None:
        params['stats'] = kwargs.get('statType',kwargs.get('types',kwargs.get('type')))
    if season is None:
        params['season'] = default_season()
    else:
        params['season'] = season

    if league == "all":
        params['leagueIds'] = "103,104"
    elif str(league).lower() in ("al","american","103"):
        params['leagueIds'] = 103
    elif str(league).lower() in ("nl","national","104"):
        params['leagueIds'] = 104
    
    if type(hydrate) is str:
        params['hydrate'] = hydrate

    url = c.BASE + f"/teams/stats?"
    
    if kwargs.get('url_only'):
        req = requests.Request("GET",url,params=params)
        prepared_url = req.prepare().url
        if kwargs.get("log"):
            print(prepared_url)
        return prepared_url
    
    resp = requests.get(url,params=params)
    if kwargs.get("log"):
        print(resp.url)
    
    # return parsing._new_stat_collection(response=resp.json())
    return dclass.StatTypeCollection.from_json(resp.json())

def league_leaders(season=None,statGroup=None,playerPool="Qualified"):
    """Get league leaders for hitting & pitching

    season : int or str
        season/year ID to get stats

    statGroup : str
        stat group(s) to retrieve stats for (hitting, pitching, fielding, etc.)

    playerPool : str
        filter results by pool
            - "All"
            - "Qualified"
            - "Rookies"
            - "Qualified_rookies"
    
    """
    if season is None:
        season = default_season()

    if statGroup is None:
        statGroup = 'hitting,pitching'

    url = c.BASE + f"/stats?stats=season&season={season}&group={statGroup}&playerPool={playerPool}"

    resp = requests.get(url)

    resp_json = resp.json()

    hit_cols = ['rank','season','position','player_mlbam','player_name','team_mlbam','team_name','league_mlbam','league_name','G','GO','AO','R','2B','3B','HR','SO','BB','IBB','H','HBP','AVG','AB','OBP','SLG','OPS','CS','SB','SB%','GIDP','P','PA','TB','RBI','LOB','sB','sF','BABIP','GO/AO','CI','AB/HR']
    pit_cols = ['rank','season','position','player_mlbam','player_name','team_mlbam','team_name','league_mlbam','league_name','G','GS','GO','AO','R','2B','3B','HR','SO','BB','IBB','H','HBP','AVG','AB','OBP','SLG','OPS','CS','SB','SB%','GIDP','P','ERA','IP','W','L','SV','SVO','HLD','BS','ER','WHIP','BF','O','GP','CG','ShO','K','K%','HB','BK','WP','PK','TB','GO/AO','W%','P/Inn','GF','SO:BB','SO/9','BB/9','H/9','R/9','HR/9','IR','IRS','CI','sB','sF']

    top_hitters = []
    top_pitchers = []

    for g in resp_json['stats']:
        sg = g.get("group",{}).get("displayName")
        # st = g.get("type",{}).get("displayName")
        if sg == "hitting":
            for s in g.get("splits",[{}]):
                stats = s.get("stat")

                rank = s.get('rank')
                season = s.get("season",'-')
                player = s.get("player",{})
                player_mlbam = player.get("id","")
                player_name = player.get("fullName","")
                team = s.get("team",{})
                team_mlbam = team.get("id","")
                team_name = team.get("name","")
                league = s.get("league",{})
                league_mlbam = league.get("id","")
                league_name = league.get("name","")
                position = s.get("position",{}).get("abbreviation",'-')

                stats["rank"] = rank
                stats["season"] = season
                stats["position"] = position
                stats["player_mlbam"] = player_mlbam
                stats["player_name"] = player_name
                stats["team_mlbam"] = team_mlbam
                stats["team_name"] = team_name
                stats["league_mlbam"] = league_mlbam
                stats["league_name"] = league_name
                
                top_hitters.append(pd.Series(stats))

        elif sg == "pitching":
            for s in g.get("splits",[{}]):
                stats = s.get("stat")

                rank = s.get('rank')
                season = s.get("season",'-')
                player = s.get("player",{})
                player_mlbam = player.get("id","")
                player_name = player.get("fullName","")
                team = s.get("team",{})
                team_mlbam = team.get("id","")
                team_name = team.get("name","")
                league = s.get("league",{})
                league_mlbam = league.get("id","")
                league_name = league.get("name","")
                position = s.get("position",{}).get("abbreviation",'-')

                stats["rank"] = rank
                stats["season"] = season
                stats["position"] = position
                stats["player_mlbam"] = player_mlbam
                stats["player_name"] = player_name
                stats["team_mlbam"] = team_mlbam
                stats["team_name"] = team_name
                stats["league_mlbam"] = league_mlbam
                stats["league_name"] = league_name
                
                top_pitchers.append(pd.Series(stats))

    hit_df = pd.DataFrame(data=top_hitters).rename(columns=c.STATDICT)[hit_cols]
    pitch_df = pd.DataFrame(data=top_pitchers).rename(columns=c.STATDICT)[pit_cols]

    return {"hitting":hit_df,"pitching":pitch_df}

def season_standings(season=None,standingsType=None,**kwargs):
    if season is None:
        season = default_season()
    if standingsType is None:
        standingsType = "byDivision"
    params = {
        "leagueId":"103,104",
        "season":season,
        "standingsTypes":standingsType,
        "hydrate":"team"
    }
    url = c.BASE + f"/standings?"
    if kwargs.get('url_only'):
        req = requests.Request("GET",url,params=params)
        return req.prepare().url
    
    resp = requests.get(url,params=params)
    
    parsed_data = parsing._parse_season_standings_data(resp.json())
    
    return pd.DataFrame(parsed_data)
    
# ===============================================================
# MISC Functions
# ===============================================================
def find_team(query,season=None):
    """Search for teams by name. *Uses local db storage

    Paramaters
    ----------
    query : str
        keywords to search for in the 'teams' data (e.g. "white sox" or 
        "Philadelphia")

    season : int or str, optional
        filter results by season

    `season=2005` -> return results from 2005
    
    `season=None` -> return results from the season in progress or the 
    last one completed (Default)
    
    `season='all'` -> return results from all seasons 
    (season filter not applied)
    
    """

    df = mlbdata.get_teams_df()
    
    if season is None:
        season = default_season()
        df = df[df["season"]==int(season)]
    elif season == 'all':
        pass
    else:
        df = df[df["season"]==int(season)]

    query = query.lower()
    rows = []
    for idx,row in df.iterrows():
        if query in row['name_full'].lower():
            rows.append(row)

    return pd.DataFrame(rows).reset_index(drop=True)

def find_venue(query):
    """Search for venues by name

    Paramaters
    ----------
    query : str
        keywords to search for in the 'venues' data (e.g. "Comiskey Park")

    """

    df = mlbdata.get_venues_df()

    query = query.lower()

    df['vname'] = df['name'].str.lower()

    df = df[df['vname'].str.contains(query,case=False,regex=False)]

    return df.drop(columns="vname")
  
def play_search(
    mlbam,
    season=None,
    statGroup=None,
    opposingTeamId=None,
    eventTypes=None,
    pitchTypes=None,
    gameTypes=None,
    **kwargs):
    """Search for any individual play 2008 and later

    Parameters
    ----------
    mlbam : str or int
        player's official MLB ID
    
    seasons : str or int, optional
        filter by season

    statGroup : str, optional
        filter results by stat group ('hitting','pitching','fielding')

    eventTypes : str, optional
        type of play events to filter

    pitchTypes : str, optional
        type of play events to filter

    gameType : str, optional
        filter results by game type

    """

    # Allows the user to lookup "playLog" or "pitchLog". URL is flexible since both statTypes are very similar

    if kwargs.get('statType','playLog').lower() == 'pitchlog':
        statType = 'pitchLog'
    else:
        statType = 'playLog'

    if kwargs.get('season') is not None: season = kwargs['season']
    if kwargs.get('group') is not None: statGroup = kwargs['group']
    elif kwargs.get('groups') is not None: statGroup = kwargs['groups']
    elif kwargs.get('statGroups') is not None: statGroup = kwargs['statGroups']
    if kwargs.get('opposingTeamIds') is not None: opposingTeamId = kwargs['opposingTeamIds']
    if kwargs.get('eventType') is not None: eventTypes = kwargs['eventType']
    if kwargs.get('pitchType') is not None: pitchTypes = kwargs['pitchType']
    if kwargs.get('gameType') is not None: gameTypes = kwargs['gameType']

    if season is None:
        season = default_season()
    season = int(season)
    
    params = {
        'stats':statType,
        'season':season,
        'hydrate':'hitData,pitchData'
    }

    if opposingTeamId is not None:
        params['opposingTeamId'] = opposingTeamId

    if eventTypes is not None:
        params['eventType'] = eventTypes

    if pitchTypes is not None:
        params['pitchType'] = pitchTypes

    if gameTypes is not None:
        params['gameType'] = gameTypes

    if statGroup is not None:
        params['group'] = statGroup

    teams_df = mlbdata.get_teams_df(year=season).set_index('mlbam')

    url = c.BASE + f'/people/{mlbam}/stats'
    response = requests.get(url,params=params)
    resp = response.json()

    if kwargs.get('_log') is True:
        print('SEASON:\n')
        print(season)
        print('TEAMS:\n')
        print(teams_df)
        print('\nREQUEST URL:\n')
        print(response.url)
        print('----------------------------\n')

    # log = resp["stats"][0]
    all_logs = []
    for log_stats in resp['stats']:
        all_logs.append(log_stats)

    plays = []

    for log in all_logs:
        if statGroup is None:
            statGroup = log.get('group',{}).get('displayName')
        if statGroup == 'hitting':
            # plays = []
            for split in log['splits']:
                season = split['season']
                date = split['date']
                gameType = split['gameType']
                gamePk = split['game']['gamePk']
                batter = split['batter']
                team = split['team']
                opponent = split['opponent']
                pitcher = split['pitcher']
                play = split.get('stat',{}).get('play',{})
                play_id = play.get('playId')
                details = play.get('details',{})
                eventType = details.get('event','-')
                event = details.get('call',{}).get('description','-')
                description = details.get('description','-')
                isInPlay = details.get('isInPlay','-')
                isStrike = details.get('isStrike','-')
                isBall = details.get('isBall','-')
                isAtBat = details.get('isAtBat','-')
                isPlateAppearance = details.get('isPlateAppearance','-')
                pitchType = details.get('type',{}).get('description','-')
                batterStands = details.get('batSide',{}).get('code')
                pitcherThrows = details.get('pitchHand',{}).get('code')
                balls = play.get('count',{}).get('balls','-')
                strikes = play.get('count',{}).get('strikes','-')
                outs = play.get('count',{}).get('outs','-')
                inning = play.get('count',{}).get('inning','-')
                runnerOnFirst = play.get('count',{}).get('runnerOn1b','-')
                runnerOnSecond = play.get('count',{}).get('runnerOn2b','-')
                runnerOnThird = play.get('count',{}).get('runnerOn3b','-')

                hitData = play.get('hitData',{})
                pitchData = play.get('pitchData',{})
                
                if split.get('isHome',False) is True:
                    away_mlbam = opponent['id']
                    home_mlbam = team['id']
                else:
                    away_mlbam = team['id']
                    home_mlbam = opponent['id']
                    
                game_label = f'{teams_df.loc[away_mlbam]["mlbID"]} @ {teams_df.loc[home_mlbam]["mlbID"]}'
                
                pitch_info = [
                    play_id,
                    batter['fullName'],
                    batter['id'],
                    pitcher['fullName'],
                    pitcher['id'],
                    pitchType,
                    pitchData.get('coordinates',{}).get('x','-'),
                    pitchData.get('coordinates',{}).get('y','-'),
                    pitchData.get('startSpeed','-'),
                    pitchData.get('strikeZoneTop','-'),
                    pitchData.get('strikeZoneBottom','-'),
                    pitchData.get('zone','-'),
                    hitData.get('launchSpeed','-'),
                    hitData.get('launchAngle','-'),
                    hitData.get('totalDistance','-'),
                    hitData.get('trajectory','-'),
                    hitData.get('coordinates',{}).get('landingPosX','-'),
                    hitData.get('coordinates',{}).get('landingPosY','-'),
                    eventType,
                    event,
                    season,
                    date,
                    gameType,
                    gamePk,
                    balls,
                    strikes,
                    outs,
                    inning,
                    runnerOnFirst,
                    runnerOnSecond,
                    runnerOnThird,
                    description,
                    isInPlay,
                    isStrike,
                    isBall,
                    isAtBat,
                    isPlateAppearance,
                    batterStands,
                    pitcherThrows,
                    team['name'],
                    team['id'],
                    opponent['name'],
                    opponent['id'],
                    away_mlbam,
                    home_mlbam,
                    game_label,
                    ]

                plays.append(pitch_info)

        elif statGroup == 'pitching':
            # plays = []
            for split in log['splits']:
                season = split['season']
                date = split['date']
                gameType = split['gameType']
                gamePk = split['game']['gamePk']
                batter = split['batter']
                team = split['team']
                opponent = split['opponent']
                pitcher = split['pitcher']
                play = split.get('stat',{}).get('play',{})
                play_id = play.get('playId')
                details = play.get('details',{})
                eventType = details.get('event','-')
                event = details.get('call',{}).get('description','-')
                description = details.get('description','-')
                isInPlay = details.get('isInPlay','-')
                isStrike = details.get('isStrike','-')
                isBall = details.get('isBall','-')
                isAtBat = details.get('isAtBat','-')
                isPlateAppearance = details.get('isPlateAppearance','-')
                pitchType = details.get('type',{}).get('description','-')
                batterStands = details.get('batSide',{}).get('code')
                pitcherThrows = details.get('pitchHand',{}).get('code')
                balls = play.get('count',{}).get('balls','-')
                strikes = play.get('count',{}).get('strikes','-')
                outs = play.get('count',{}).get('outs','-')
                inning = play.get('count',{}).get('inning','-')
                runnerOnFirst = play.get('count',{}).get('runnerOn1b','-')
                runnerOnSecond = play.get('count',{}).get('runnerOn2b','-')
                runnerOnThird = play.get('count',{}).get('runnerOn3b','-')

                hitData = play.get('hitData',{})
                pitchData = play.get('pitchData',{})
                
                if split.get('isHome',False) is True:
                    away_mlbam = opponent['id']
                    home_mlbam = team['id']
                else:
                    away_mlbam = team['id']
                    home_mlbam = opponent['id']
                    
                game_label = f'{teams_df.loc[away_mlbam]["mlbID"]} @ {teams_df.loc[home_mlbam]["mlbID"]}'

                pitch_info = [
                    play_id,
                    batter['fullName'],
                    batter['id'],
                    pitcher['fullName'],
                    pitcher['id'],
                    pitchType,
                    pitchData.get('coordinates',{}).get('x','-'),
                    pitchData.get('coordinates',{}).get('y','-'),
                    pitchData.get('startSpeed','-'),
                    pitchData.get('strikeZoneTop','-'),
                    pitchData.get('strikeZoneBottom','-'),
                    pitchData.get('zone','-'),
                    hitData.get('launchSpeed','-'),
                    hitData.get('launchAngle','-'),
                    hitData.get('totalDistance','-'),
                    hitData.get('trajectory','-'),
                    hitData.get('coordinates',{}).get('landingPosX','-'),
                    hitData.get('coordinates',{}).get('landingPosY','-'),
                    eventType,
                    event,
                    season,
                    date,
                    gameType,
                    gamePk,
                    balls,
                    strikes,
                    outs,
                    inning,
                    runnerOnFirst,
                    runnerOnSecond,
                    runnerOnThird,
                    description,
                    isInPlay,
                    isStrike,
                    isBall,
                    isAtBat,
                    isPlateAppearance,
                    batterStands,
                    pitcherThrows,
                    team['name'],
                    team['id'],
                    opponent['name'],
                    opponent['id'],
                    away_mlbam,
                    home_mlbam,
                    game_label,
                    ]

                plays.append(pitch_info)
            
    columns = [
        'play_id',
        'batter_name',
        'batter_mlbam',
        'pitcher_name',
        'pitcher_mlbam',
        'pitch_type',
        'pitchX',
        'pitchY',
        'startSpeed',
        'strikeZoneTop',
        'strikeZoneBottom',
        'zone',
        'launchSpeed',
        'launchAngle',
        'totalDistance',
        'trajectory',
        'hitX',
        'hitY',
        'event_type',
        'event',
        'season',
        'date',
        'gameType',
        'gamePk',
        'balls',
        'strikes',
        'outs',
        'inning',
        'runnerOnFirst',
        'runnerOnSecond',
        'runnerOnThird',
        'description',
        'isInPlay',
        'isStrike',
        'isBall',
        'isAtBat',
        'isPlateAppearance',
        'batterStands',
        'pitcherThrows',
        'team_name',
        'team_mlbam',
        'opponent_name',
        'opponent_mlbam',
        'away_mlbam',
        'home_mlbam',
        'game_label',
        ]
    
    df = pd.DataFrame(data=plays,columns=columns)
    df = df.iloc[::-1]
    
    
    return df

def pitch_search(mlbam,seasons=None,statGroup=None,opposingTeamId=None,eventTypes=None,pitchTypes=None):
    """Search for any individual pitch 2008 and later

    Parameters
    ----------
    mlbam : str or int
        Player's official "MLB Advanced Media" ID
    
    seasons : str or int, optional
        filter by season

    statGroup : str, optional
        filter results by stat group ('hitting','pitching','fielding')

    eventTypes : str, optional
        type of play events to filter

    pitchTypes : str, optional
        type of play events to filter

    gameType : str, optional
        filter results by game type

    """
    if seasons is None:
        seasons = default_season()

    queryString = [f"seasons={seasons}",f"hydrate=hitData,pitchData"]

    if opposingTeamId is None:
        pass
    else:
        queryString.append(f"opposingTeamId={opposingTeamId}")

    if eventTypes is None:
        pass
    else:
        queryString.append(f"eventType={eventTypes}")

    if pitchTypes is None:
        pass
    else:
        queryString.append(f"pitchTypes={pitchTypes}")

    if statGroup is None:
        pass
    else:
        queryString.append(f"group={statGroup}")


    queryString = "&".join(queryString)
    
    url = c.BASE + f"/people/{mlbam}/stats?stats=pitchLog&{queryString}"


    response = requests.get(url)
    
    log = response.json()["stats"][0]
    
    if statGroup == "hitting":
        pitches = []
        for split in log["splits"]:
            season = split["season"]
            date = split["date"]
            gameType = split["gameType"]
            gamePk = split["game"]["gamePk"]
            batter = split["batter"]
            team = split["team"]
            opponent = split["opponent"]
            pitcher = split["pitcher"]
            play = split.get("stat",{}).get("play",{})
            play_id = play.get("playId")
            details = play.get("details",{})
            eventType = details.get("event","-")
            event = details.get("call",{}).get("description","-")
            description = details.get("description","-")
            isInPlay = details.get("isInPlay","-")
            isStrike = details.get("isStrike","-")
            isBall = details.get("isBall","-")
            isAtBat = details.get("isAtBat","-")
            isPlateAppearance = details.get("isPlateAppearance","-")
            pitchType = details.get("type",{}).get("description","-")
            batterStands = details.get("batSide",{}).get("code")
            pitcherThrows = details.get("pitchHand",{}).get("code")
            balls = play.get("count",{}).get("balls","-")
            strikes = play.get("count",{}).get("strikes","-")
            outs = play.get("count",{}).get("outs","-")
            inning = play.get("count",{}).get("inning","-")
            runnerOnFirst = play.get("count",{}).get("runnerOn1b","-")
            runnerOnSecond = play.get("count",{}).get("runnerOn2b","-")
            runnerOnThird = play.get("count",{}).get("runnerOn3b","-")

            hitData = play.get("hitData",{})
            
            pitchData = play.get("pitchData",{})

            pitch_info = [
                play_id,
                batter["fullName"],
                batter["id"],
                pitcher["fullName"],
                pitcher["id"],
                pitchType,
                pitchData.get("coordinates",{}).get("x","-"),
                pitchData.get("coordinates",{}).get("y","-"),
                pitchData.get("startSpeed","-"),
                pitchData.get("strikeZoneTop","-"),
                pitchData.get("strikeZoneBottom","-"),
                pitchData.get("zone","-"),
                hitData.get("launchSpeed","-"),
                hitData.get("launchAngle","-"),
                hitData.get("totalDistance","-"),
                hitData.get("trajectory","-"),
                hitData.get("coordinates",{}).get("landingPosX","-"),
                hitData.get("coordinates",{}).get("landingPosY","-"),
                eventType,
                event,
                season,
                date,
                gameType,
                gamePk,
                date,
                balls,
                strikes,
                outs,
                inning,
                runnerOnFirst,
                runnerOnSecond,
                runnerOnThird,
                description,
                isInPlay,
                isStrike,
                isBall,
                isAtBat,
                isPlateAppearance,
                batterStands,
                pitcherThrows,
                team["name"],
                team["id"],
                opponent["name"],
                opponent["id"]]

            pitches.append(pitch_info)

    elif statGroup == "pitching":
        pitches = []
        for split in log["splits"]:
            season = split["season"]
            date = split["date"]
            gameType = split["gameType"]
            gamePk = split["game"]["gamePk"]
            batter = split["batter"]
            team = split["team"]
            opponent = split["opponent"]
            pitcher = split["pitcher"]
            play = split.get("stat",{}).get("play",{})
            play_id = play.get("playId")
            details = play.get("details",{})
            eventType = details.get("event","-")
            event = details.get("call",{}).get("description","-")
            description = details.get("description","-")
            isInPlay = details.get("isInPlay","-")
            isStrike = details.get("isStrike","-")
            isBall = details.get("isBall","-")
            isAtBat = details.get("isAtBat","-")
            isPlateAppearance = details.get("isPlateAppearance","-")
            pitchType = details.get("type",{}).get("description","-")
            batterStands = details.get("batSide",{}).get("code")
            pitcherThrows = details.get("pitchHand",{}).get("code")
            balls = play.get("count",{}).get("balls","-")
            strikes = play.get("count",{}).get("strikes","-")
            outs = play.get("count",{}).get("outs","-")
            inning = play.get("count",{}).get("inning","-")
            runnerOnFirst = play.get("count",{}).get("runnerOn1b","-")
            runnerOnSecond = play.get("count",{}).get("runnerOn2b","-")
            runnerOnThird = play.get("count",{}).get("runnerOn3b","-")

            hitData = play.get("hitData",{})
            
            pitchData = play.get("pitchData",{})

            pitch_info = [
                play_id,
                batter["fullName"],
                batter["id"],
                pitcher["fullName"],
                pitcher["id"],
                pitchType,
                pitchData.get("coordinates",{}).get("x","-"),
                pitchData.get("coordinates",{}).get("y","-"),
                pitchData.get("startSpeed","-"),
                pitchData.get("strikeZoneTop","-"),
                pitchData.get("strikeZoneBottom","-"),
                pitchData.get("zone","-"),
                hitData.get("launchSpeed","-"),
                hitData.get("launchAngle","-"),
                hitData.get("totalDistance","-"),
                hitData.get("trajectory","-"),
                hitData.get("coordinates",{}).get("landingPosX","-"),
                hitData.get("coordinates",{}).get("landingPosY","-"),
                eventType,
                event,
                season,
                date,
                gameType,
                gamePk,
                date,
                balls,
                strikes,
                outs,
                inning,
                runnerOnFirst,
                runnerOnSecond,
                runnerOnThird,
                description,
                isInPlay,
                isStrike,
                isBall,
                isAtBat,
                isPlateAppearance,
                batterStands,
                pitcherThrows,
                team["name"],
                team["id"],
                opponent["name"],
                opponent["id"]]

            pitches.append(pitch_info)
           
    columns = [
        'play_id',
        'batter_name',
        'batter_mlbam',
        'pitcher_name',
        'pitcher_mlbam',
        'pitch_type',
        'pitchX',
        'pitchY',
        'startSpeed',
        'strikeZoneTop',
        'strikeZoneBottom',
        'zone',
        'launchSpeed',
        'launchAngle',
        'totalDistance',
        'trajectory',
        'hitX',
        'hitY',
        'event_type',
        'event',
        'season',
        'date',
        'gameType',
        'gamePk',
        'date',
        'balls',
        'strikes',
        'outs',
        'inning',
        'runnerOnFirst',
        'runnerOnSecond',
        'runnerOnThird',
        'description',
        'isInPlay',
        'isStrike',
        'isBall',
        'isAtBat',
        'isPlateAppearance',
        'batterStands',
        'pitcherThrows',
        'team_name',
        'team_mlbam',
        'opponent_name',
        'opponent_mlbam']
    
    df = pd.DataFrame(data=pitches,columns=columns)
    
    return df

def game_search(
    mlbam:int=None,
    date=None,
    startDate=None,
    endDate=None,
    season=None,
    gameType=None) -> pd.DataFrame:
    """Search for a games
    
    Paramaters:
    -----------
    mlbam : int
        Team's official "MLB Advanced Media" ID
    
    date : str
        Search for games by date (fmt: YYYY-mm-dd)

    NOTE: "date" will be ignored if "startDate" and "endDate" are used. 
    If only "startDate" is used, "endDate" will default to today's date
    
    NEED TO ADD PARAMETER FOR 'OPPONENT TEAM'
    
    """
    
    params = {'teamId':mlbam,
              'date':date,
              'startDate':startDate,
              'endDate':endDate,
              'season':season,
              'gameType':gameType,
              'sportId':1
              }
    
    url = c.BASE + f"/schedule"
    response = requests.get(url,params=params)
    all_results = []

    for d in response.json()["dates"]:
        for g in d["games"]:
            away_name   = g.get("teams",{}).get("away",{}).get("team",{}).get("name","-")
            away_mlbam  = g.get("teams",{}).get("away",{}).get("team",{}).get("id","-")
            home_name   = g.get("teams",{}).get("home",{}).get("team",{}).get("name","-")
            home_mlbam  = g.get("teams",{}).get("home",{}).get("team",{}).get("id","-")
            game_date   = g.get("officialDate","-")
            game_date   = dt.datetime.strptime(game_date,r'%Y-%m-%d')
            game_pk     = g.get("gamePk","-")
            game_type   = g.get("gameType","-")
            status      = g.get("status",{}).get("detailedState","-")
            venue       = g.get("venue",{}).get("name","-")
            start_time  = g.get("gameDate","-")
            result      = [game_pk,
                           away_mlbam,
                           away_name,
                           home_mlbam,
                           home_name,
                           game_date,
                           game_type,
                           venue,
                           start_time,
                           status]
            all_results.append(result)
    
    df = pd.DataFrame(data=all_results,columns=["gamePk",
                                                "away_mlbam",
                                                "away_name",
                                                "home_mlbam",
                                                "home_name",
                                                "date",
                                                "type",
                                                "venue",
                                                "start_time",
                                                "status"])
    if len(df) == 0:
        # print("No games found")
        return pd.DataFrame()
    return df

def last_game(mlbam):
    """Get basic game information for a team's last game
    
    Parameters:
    -----------
    mlbam : int | str
        Official MLB Advanced Media ID for team
    
    """
    teamID = str(mlbam)

    season_info = mlbdata.get_season_info()
    
    if season_info['in_progress'] is None:
        m = 12
        d = 1
        y = season_info['last_completed']
        season = y
    else:
        m = curr_date.month
        d = curr_date.day
        y = curr_date.year
        season = season_info['in_progress']

    url = c.BASE + f"/teams/{teamID}?hydrate=previousSchedule(date={m}/{d}/{y},inclusive=True,limit=1,season={season},gameType=[S,R,D,W,F,C,L])"

    resp = requests.get(url)

    result = resp.json()["teams"][0]["previousGameSchedule"]["dates"][0]["games"][0]
    gamePk = result.get("gamePk","")
    gameType = result.get("gameType","")
    gameDate = result.get("gameDate","")[:10]
    away_mlbam = result.get("teams",{}).get("away",{}).get("team",{}).get("id","-")
    home_mlbam = result.get("teams",{}).get("home",{}).get("team",{}).get("id","-")
    away = result.get("teams",{}).get("away",{}).get("team",{}).get("name","-")
    home = result.get("teams",{}).get("home",{}).get("team",{}).get("name","-")
    if str(away_mlbam) == str(teamID):
        opp = f"@ {home}"
        opp_mlbam = home_mlbam
    elif str(home_mlbam) == str(teamID):
        opp = f"vs {away}"
        opp_mlbam = away_mlbam
    df = pd.DataFrame(
        data=[[gamePk,opp,opp_mlbam,gameDate,gameType]],
        columns=("gamePk","opponent","opp_mlbam","date","gameType"))

    return df

def next_game(mlbam):
    """Get basic game information for a team's next game
    
    Parameters:
    -----------
    mlbam : int | str
        Official MLB Advanced Media ID for team
        
    """
    teamID = mlbam

    m = curr_date.month
    d = curr_date.day
    y = curr_date.year

    try:
        url = c.BASE + f"/teams/{teamID}?hydrate=nextSchedule(date={m}/{d}/{y},inclusive=True,limit=1,season={y},gameType=[S,R,P])"
        response = requests.get(url)
        results = response.json()["teams"][0]["nextGameSchedule"]["dates"][0]["games"]
    except:
        url = c.BASE + f"/teams/{teamID}?hydrate=nextSchedule(date={m}/{d}/{y},inclusive=True,limit=1,season={y+1},gameType=[S,R,P])"
        response = requests.get(url)
        results = response.json()["teams"][0]["nextGameSchedule"]["dates"][0]["games"]

    result = results[0]
    gamePk = result.get("gamePk","")
    gameType = result.get("gameType","")
    gameDate = result.get("gameDate","")[:10]
    away_mlbam = result.get("teams",{}).get("away",{}).get("team",{}).get("id","-")
    home_mlbam = result.get("teams",{}).get("home",{}).get("team",{}).get("id","-")
    away = result.get("teams",{}).get("away",{}).get("team",{}).get("name","-")
    home = result.get("teams",{}).get("home",{}).get("team",{}).get("name","-")
    if str(away_mlbam) == str(teamID):
        opp = f"@ {home}"
        opp_mlbam = home_mlbam
    elif str(home_mlbam) == str(teamID):
        opp = f"vs {away}"
        opp_mlbam = away_mlbam
    df = pd.DataFrame(
        data=[[gamePk,opp,opp_mlbam,gameDate,gameType]],
        columns=("gamePk","opponent","opp_mlbam","date","gameType"))
    
    return df

def schedule(mlbam=None,season=None,date=None,startDate=None,endDate=None,gameType=None,opponentId=None,**kwargs) -> pd.DataFrame:
    """Get game schedule data.

    Parameters
    ----------
    mlbam : int | str, optional
        Official "MLB Advanced Media" ID for team

    season : int or str, optional
        get schedule information for a specific season

    date : str, optional (format, "YYYY-mm-dd")
        get the schedule for a specific date
    
    startDate : str, optional (format: "YYYY-mm-dd")
        the starting date to filter your search
    
    endDate : str, optional (format: "YYYY-mm-dd")
        the ending date to filter your search
    
    gameType : str, optional
        filter results by specific game type(s)
    
    opponentId : str or int, optional
        specify an opponent's team mlbam/id to get results between two opponents

    Other Parameters
    ----------------
    tz : str, optional (Defaults to Central time)
        keyword argument to specify which timezone to view game times
    
    hydrate : str, optional
        retrieve additional data (example hydrations - "linescore,person,decisions,lineups(person),probablePitcher")

    """

    url = c.BASE + "/schedule?"

    params = {
        "sportId":1
    }
    if kwargs.get('teamId') is not None:
        mlbam = kwargs['teamId']
    if startDate is not None and endDate is not None:
        # params['startDate']   = dt.datetime.strptime(startDate,r"%m/%d/%Y").strftime(r"%Y-%m-%d")
        params['startDate']     = startDate
        # params['endDate']     = dt.datetime.strptime(endDate,r"%m/%d/%Y").strftime(r"%Y-%m-%d")
        params['endDate']       = endDate

    else:
        if date is None and season is None:
            season_info = mlbdata.get_season_info()
            if season_info['in_progress'] is None:
                season = season_info['last_completed']
            else:
                season = season_info['in_progress']

            params['season'] = season

        elif date is not None:
            # date = dt.datetime.strptime(date,r"%m/%d/%Y").strftime(r"%Y-%m-%d")
            params['date'] = date
        
        elif season is not None:
            params['season'] = season
    
    if gameType is not None:
        if type(gameType) is list:
            gtype_str = ",".join(gameType).upper()
        elif type(gameType) is str:
            gtype_str = gameType.upper().replace(" ","")
        params["gameType"] = gtype_str

    if mlbam is not None:
        params['teamId'] = mlbam
    
    tz = kwargs.get("tz",kwargs.get("timezone"))
    tz = get_tzinfo(tz_string=tz)
    
    if kwargs.get("oppTeamId") is not None:
        opponentId = kwargs["oppTeamId"]
    elif kwargs.get("opponentTeamId") is not None:
        opponentId = kwargs["opponentTeamId"]
    elif kwargs.get("oppId") is not None:
        opponentId = kwargs["oppId"]
    if opponentId is not None:
        params["opponentId"] = None

    if kwargs.get("hydrate") is not None:
        hydrate = kwargs["hydrate"]
        params["hydrate"] = hydrate
    
    if kwargs.get('url_only'):
        req = requests.Request("GET",url,params=params)
        prepared_url = req.prepare().url
        return prepared_url
    resp = requests.get(url,params=params)

    if kwargs.get('log') is True:
        print("\n================")
        print(resp.url)
        print("================\n")

    parsed_data = parsing._parse_schedule_data(json_response=resp.json(),selected_timezone=tz)
    df = pd.DataFrame(data=parsed_data)
    official_dt_col = pd.to_datetime(df["date_official"] + " " + df["game_start"],format=r"%Y-%m-%d %I:%M %p")
    df.insert(0,"official_dt",official_dt_col)
    return df

def games_today():
    date_str = dt.datetime.today().strftime(r'%Y-%m-%d')
    sched = schedule(date=date_str)
    return sched

def scores(return_list=False):
    today = dt.datetime.today()
    date_str = today.strftime(r'%Y-%m-%d')
    games = schedule(date=date_str,hydrate='linescore')
    tms = mlbdata.get_teams_df(year=today.year).set_index('mlbam')
    score_list = []
    gm_str = '{:3} {:>2}  vs  {:2} {:3} | {:6} | {:12}'
    for idx,gm in games.iterrows():
        aw_mlbam = int(gm.away_mlbam)
        hm_mlbam = int(gm.home_mlbam)
        aw_abbrv = tms.loc[aw_mlbam]['mlbID']
        aw_score = gm.away_score
        hm_abbrv = tms.loc[hm_mlbam]['mlbID']
        hm_score = gm.home_score
        gm_start = f'{gm.game_start} ET'
        
        gpk = gm.gamePk
        
        gm_state = gm.abstract_state
        inn = gm.inn
        inn_ord = gm.inn_ord
        inn_state = gm.inn_state
        inn_half = gm.inn_half
        
        if gm_state == 'Final':
            gm_deets = 'Final'
        elif gm_state == 'Live':
            gm_deets = f'{inn_state[:3]} {inn_ord}'
        else:
            gm_deets = gm_start
        
        row_str = gm_str.format(aw_abbrv,aw_score,hm_score,hm_abbrv,gm_deets,gpk)
        score_list.append(row_str)
    
    if return_list is True:
        return '\n'.join(score_list)
    else:
        print('\n'.join(score_list))

def game_highlights(
    mlbam=None,
    date=None,
    startDate=None,
    endDate=None,
    season=None,
    month=None) -> pd.DataFrame:
    """
    Get video urls of team highlights for a specific date during the regular 
    season.

    Parameters:
    -----------
    mlbam : int | str
        Team's official "MLB Advanced Media" ID
    
    date : str, conditionally required (fmt: 'YYYY-mm-dd')
        Search for games on a specific date
        
    startDate : str, conditionally required (fmt: 'YYYY-mm-dd')
        Search games AFTER or on a certain date
        
    startDate : str, conditionally required (fmt: 'YYYY-mm-dd')
        Search games BEFORE or on a certain date
    
    season : int
        Search games by season
    """

    hydrations = "game(content(media(all),summary,gamenotes,highlights(highlights)))"
    if date is not None:
        url = c.BASE + f"/schedule?sportId=1&teamId={mlbam}&date={date}&hydrate={hydrations}"
    elif month is not None:
        if season is not None:
            month = str(month)
            if month.isdigit():
                next_month_start = dt.datetime(year=int(season),month=int(month)+1,day=1)
                startDate = dt.datetime(year=int(season),month=int(month),day=1)
                endDate = next_month_start - dt.timedelta(days=1)
                startDate = startDate.strftime(r"%Y-%m-%d")
                endDate = endDate.strftime(r"%Y-%m-%d")
            else:
                if len(month) == 3:
                    month = dt.datetime.strptime(month,r"%b").month
                else:
                    month = dt.datetime.strptime(month,r"%B").month

                next_month_start = dt.datetime(year=int(season),month=int(month)+1,day=1)
                startDate = dt.datetime(year=int(season),month=int(month),day=1)
                endDate = next_month_start - dt.timedelta(days=1)
                startDate = startDate.strftime(r"%Y-%m-%d")
                endDate = endDate.strftime(r"%Y-%m-%d")

            url = c.BASE + f"/schedule?sportId=1&teamId={mlbam}&startDate={startDate}&endDate={endDate}&hydrate={hydrations}"

        else:
            print("Must specify a 'season' if using 'month' param")
            return None
    elif season is not None:
        url = c.BASE + f"/schedule?sportId=1&teamId={mlbam}&season={season}&hydrate={hydrations}"

    elif startDate is not None:
        if endDate is not None:
            startDate = dt.datetime.strptime(startDate,r"%m/%d/%Y").strftime(r"%Y-%m-%d")
            endDate = dt.datetime.strptime(endDate,r"%m/%d/%Y").strftime(r"%Y-%m-%d")

            url = c.BASE + f"/schedule?sportId=1&teamId={mlbam}&startDate={startDate}&endDate={endDate}&hydrate={hydrations}"

        else:
            print("Params 'startDate' & 'endDate' must be used together")
            return None
    else:
        print("One of params, 'date' or 'season' must be utilized")
        return None

    resp = requests.get(url)

    sched = resp.json()

    data = []
    columns = [
        "date",
        "gamePk",
        "game_num",
        "away_mlbam",
        "away_score",
        "home_mlbam",
        "home_score",
        "title",
        "blurb",
        "description",
        "url"
    ]
    for date in sched["dates"]:
        game_date = date["date"]
        games = date["games"]
        for gm in games:
            away = gm["teams"]["away"]
            home = gm["teams"]["home"]
            try:
                highlights = gm["content"]["highlights"]["highlights"]["items"]

                gamePk = gm["gamePk"]
                gameNumber = gm["gameNumber"]
                away_mlbam = away.get("team",{}).get("id")
                away_name = away.get("team",{}).get("name")
                away_score = away.get("score")
                home_mlbam = home.get("team",{}).get("id")
                home_name = home.get("team",{}).get("name")
                home_score = home.get("score")
                venue_mlbam = gm.get("venue",{}).get("id")
                venue_name = gm.get("venue",{}).get("name")

                for h in highlights:
                    h_title = h.get("title")
                    h_blurb = h.get("blurb")
                    h_desc = h.get("description")
                    playbacks = h.get("playbacks",[{}])

                    for p in playbacks:
                        playback_ext = p.get("name")
                        if playback_ext == "mp4Avc" or playback_ext == "highBit":
                            h_video_url = p.get("url")
                            break

                    data.append([
                        game_date,
                        gamePk,
                        gameNumber,
                        away_mlbam,
                        away_score,
                        home_mlbam,
                        home_score,
                        h_title,
                        h_blurb,
                        h_desc,
                        h_video_url
                    ])



            except:
                pass

    df = pd.DataFrame(data=data,columns=columns)

    return df

def get_video_link(playID:str,broadcast=None) -> str:
    if broadcast is not None:
        broadcast = str(broadcast).upper()
        url = f"https://baseballsavant.mlb.com/sporty-videos?playId={playID}&videoType={broadcast}"
    else:
        url = f"https://baseballsavant.mlb.com/sporty-videos?playId={playID}"
    resp = requests.get(url)
    soup = bs(resp.text,'lxml')
    video_tag = soup.find("video",id="sporty")
    video_source = video_tag.find("source")["src"]
    return video_source

def player_bio(mlbam:int):
    """Get short biography of player from Baseball-Reference.com's Player Bullpen pages.

    Parameters
    ----------
    mlbam : str or int
        player's official MLB ID
    
    """
    # URL to Player's Baseball-Reference page
    with requests.session() as sesh:
        url = f"https://www.baseball-reference.com/redirect.fcgi?player=1&mlb_ID={mlbam}"

        resp = sesh.get(url)

        soup = bs(resp.text,'lxml')

        # URL to Player's "Bullpen" page
        url = soup.find('a',text='View Player Info')['href']

        resp = sesh.get(url)

        soup = bs(resp.text,'lxml')

        bio_p_tags = soup.find("span",id="Biographical_Information"
                               ).findParent('h2').find_next_siblings('p')

        return bio_p_tags

def free_agents(
    season:Optional[int]=None,
    hydrate_person:Optional[bool]=None,
    sort_by=None,
    sort_asc=False) -> pd.DataFrame:
    """Get data for free agents
    
    Parameters:
    -----------
    season : int, required (Defaults to the most recent season)
        The season of play
    
    hydrate_person : bool, optional
        Whether or not to use the API's "hydrate" parameter to fetch additional bio information for each free agent entry
    
    sort_by : str, optional
        Sort the dataframe by a specific column
    
    sort_asc : bool
        Determines the direction of sorting, (if "sort_asc" param is populated ('A-Z'  / 'Z-A')
    
    """
    if season is None:
        season = default_season()
    
    params = {'season':season}
    if hydrate_person is True:
        params['hydrate'] = 'person'
    
    url = f"{c.BASE}/people/freeAgents"
    resp = requests.get(url,params=params)
    
    data = []
    for fa in resp.json()['freeAgents']:
        og_team = fa.get('originalTeam',{})
        new_team = fa.get('newTeam',{})
        notes = fa.get('notes','-')
        date_signed = fa.get('dateSigned','-')
        date_declared = fa.get('dateDeclared','-')
        pos = fa.get('position',{})
        fa_data = {
            'date_signed':date_signed,
            'date_declared':date_declared,
            'notes':notes,
            'og_tm_mlbam':og_team.get('id',0),
            'og_tm_name':og_team.get('name','-'),
            'new_tm_mlbam':new_team.get('id',0),
            'new_tm_name':new_team.get('name','-'),
        }
        parsed_player_data = parsing._parse_person(_obj=fa['player'])
        parsed_player_data.update(**{'pos_code':pos.get('code','-'),
                                     'pos_name':pos.get('name','-'),
                                     'pos_type':pos.get('type','-'),
                                     'pos_abbreviation':pos.get('abbreviation','-')
                                     })
        fa_data.update(**parsed_player_data)
        
        data.append(fa_data)
    
    df = pd.DataFrame.from_dict(data).drop()
    
    if sort_by is not None and type(sort_by) is str:
        return df.sort_values(by=sort_by,ascending=sort_asc).reset_index(drop=True)
    
    return df

def league(league_id:int,attr:str=None):
    df = mlbdata.get_leagues_df().set_index("mlbam")
    league_id = int(league_id)
    if attr is None:
        return df.loc[league_id]
    else:
        return df.loc[league_id][attr]