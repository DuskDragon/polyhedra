import sys
import json
import logging
import requests
import time
from datetime import datetime
from collections import defaultdict

from flask import Flask, render_template
from flask_frozen import Freezer


app = Flask(__name__)
freezer = Freezer(app)
app.config['FREEZER_DESTINATION'] = 'out/build'
app.config['FREEZER_RELATIVE_URLS'] = True

class zKillAPI():
    def __init__(self):
        self.character_list = {}
        self.history = {}
        self.most_recent_killID = 0
        self.characterName = 'Polyhedra'

        with open('data/characters.json', 'r') as fd:
            self.character_list = json.load(fd)
        if len(self.character_list) > 10:
            raise ValueError("More than 10 values in characters.json, zkill API only allows 10")
            # maybe later we will go full api insanity and pull two different 10 character calls

        #load current history
        try:
            with open('out/data/history.json', 'r') as fd:
                self.history = json.load(fd)
        except IOError:
            with open('out/data/history.json', 'a') as faild:
                json.dump([], faild)
            self.history = []
        if len(self.history) == 0:
            self.most_recent_killID = (14123136-1) #first killmail minus 1 to fetch all UPDATE THIS IN FUTURE KILLBOARDS
        else:
            self.most_recent_killID = self.history[-1]["killID"]
        logging.info('zKillAPI.most_recent_killID=' + str(self.most_recent_killID))

        #load ship_lookup (ID dictionary) json
        try:
            with open('out/data/ship_lookup.json', 'r') as fd:
                self.ship_lookup = json.load(fd)
        except IOError:
            with open('out/data/ship_lookup.json', 'a') as faild:
                json.dump({}, faild)
            self.ship_lookup = {}

        #load solarsystem_lookup (ID dictionary) json
        try:
            with open('out/data/solarsystem_lookup.json', 'r') as fd:
                self.solarsystem_lookup = json.load(fd)
        except IOError:
            with open('out/data/solarsystem_lookup.json', 'a') as faild:
                json.dump({}, faild)
            self.solarsystem_lookup = {}


    def update_kill_history(self):
        api_call_charID_list = ','.join(str(x) for x in self.character_list.values())
        api_call_frontstr = "http://zkillboard.com/api/character/"
        api_call_backstr = "/afterKillID/"+str(self.most_recent_killID)+"/orderDirection/asc/no-items/page/"
        api_call_minus_page_num = api_call_frontstr + api_call_charID_list + api_call_backstr
        current_page = 1
        print 'calling zkill: '+api_call_minus_page_num+str(current_page)+'/'
        raw_api_data = requests.get(api_call_minus_page_num+str(current_page)+'/').json()
        raw_api_pages = raw_api_data
        while len(raw_api_data) != 0: #ensure there are no further pages
            time.sleep(10) # zkill api can be slow and tends to error out
            current_page += 1
            print 'calling zkill: ' +api_call_minus_page_num+str(current_page)+'/'
            raw_api_data = requests.get(api_call_minus_page_num+str(current_page)+'/').json()
            raw_api_pages.extend(raw_api_data)
        #no more pages on the api with data
        self.history.extend(raw_api_pages)
        #for a new round of api calls, final item contains the newest killID if it exists
        self.most_recent_killID = self.history[-1]["killID"]
        logging.info('zKillAPI.most_recent_killID updated to: ' + str(self.most_recent_killID))
        return current_page

    def prune_unused_history_fields(self):
        for mail in self.history:
            mail.pop('moonID', None) #prune moon info
            mail.pop('position', None) #we don't need y,x,z in-space coords
            mail['zkb'].pop('hash', None) #prune zkill hash value
            mail['zkb'].pop('points', None) #prune points metric because it means literally nothing
            if mail.get('involved', None) == None:
                mail['involved'] = len(mail['attackers']) # save number involved because we are pruning attackers
            pruned_attackers = []
            for attacker in mail['attackers']: #keep only those on character_list or finalBlow == 1
                if attacker['finalBlow'] == 1 or attacker['characterName'] in self.character_list.keys():
                    attacker.pop('securityStatus', None) # drop sec status
                    pruned_attackers.append(attacker)
                    #save final_blow to top level location also
                    if attacker['finalBlow'] == 1:
                        mail['final_blow'] = attacker
            mail['attackers'] = pruned_attackers

    def tag_as_kill_loss_or_friendly_fire(self):
        for mail in self.history:
            #if row_type tag exists, skip this mail
            if mail.get('row_type', None) != None:
                continue
            #if one of our characters is the victim it is a loss
            if mail.get('victim', None) != None:
                if mail['victim'].get('characterName', None) in self.character_list.keys():
                    #if one of our characters is on the killmail it's not just a loss
                    #it's a friendly fire incident
                    for attacker in mail['attackers']:
                        if attacker['characterName'] in self.character_list.keys():
                            mail['row_type'] = 'row-friendlyfire'
                            break
                    if mail.get('row_type', None) == None: # if it wasn't tagged friendly fire
                        mail['row_type'] = 'row-loss'      # then it's just a loss
                else: # if one of our characters isn't teh victim then it is a kill
                    mail['row_type'] = 'row-kill'

    def tag_involved_characters(self):
        for mail in self.history:
            #if our_chracters tag exists, skip this mail
            if mail.get('our_characters', None) != None:
                continue
            #build an array of all of our characters involved
            involved = []
            for attacker in mail['attackers']:
                if attacker['characterName'] in self.character_list.keys():
                    involved.append(attacker['characterName'])
            mail['our_characters'] = involved
            mail['our_involved_html'] = ('<BR>'.join(x for x in involved))

    def kill_counts(self, killtype):
        return len([x for x in self.history if x['row_type'] == killtype])

    def engineering_number_string(self, value):
        powers = [10 ** x for x in (3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 100)]
        human_powers = ('k', 'm', 'b', 't', 'qa','qi', 'sx', 'sp', 'oct', 'non', 'dec', 'googol')
        try:
            value = int(value)
        except (TypeError, ValueError):
            return value

        if value < powers[0]:
            return str(value)
        for ordinal, power in enumerate(powers[1:], 1):
            if value < power:
                chopped = value / float(powers[ordinal - 1])
                format = ''
                if chopped < 10:
                    format = '%.2f'
                elif chopped < 100:
                    format = '%.1f'
                else:
                    format = '%i'
                return (''.join([format, human_powers[ordinal - 1]])) % chopped
        return str(value)

    def tag_formatted_values(self):
        for mail in self.history:
            #if formatted_price tag exists, skip this mail
            if mail.get('formatted_price', None) != None:
                continue
            #grab the totalValue
            mail['formatted_price'] = self.engineering_number_string(mail['zkb']['totalValue'])

    def kill_sums(self, killtype):
        r = sum(self.verify_kill(x, killtype) for x in self.history)
        return self.engineering_number_string(r)

    def verify_kill(self, k, killtype):
        if k['row_type'] in [killtype, 'row-friendlyfire']:
            if 'zkb' in k and 'totalValue' in k['zkb']:
                return k['zkb']['totalValue']
        return 0

    def format_date(self, dateval):
        year = str(int(dateval[0:4]))
        month = int(dateval[5:7])
        day = str(int(dateval[8:11]))
        monthname = ['','January', 'February', 'March', 'April', 'May', \
            'June', 'July', 'August', 'September', 'October', 'November', \
            'December']
        return monthname[month] + ' ' + day + ', ' + year

    def kills_by_date(self):
        kills = defaultdict(list)
        for kill in reversed(self.history):
            kills[kill['killTime'].split(' ')[0]].append(kill)
        kills_by_day = sorted(kills.items(), key=lambda x: x[0], reverse=True)
        result = []
        for day, killmails in kills_by_day:
            result.append((day, self.format_date(day), killmails))
        return result

    def tag_solarSystemName(self):
        for mail in self.history:
            theID = mail['solarSystemID']
            if mail.get('solarSystemName', None) != None:
                continue
            #if solarSystemID present in self.solarsystem_lookup don't call the api
            temp_solarsystem_name = self.solarsystem_lookup.get(theID, None)
            if temp_solarsystem_name != None:
                mail['solarSystemName'] = temp_solarsystem_name
            else: #better call ccp example: https://crest-tq.eveonline.com/solarsystems/30002022/
                api_call_front_str = 'https://crest-tq.eveonline.com/solarsystems/'
                print 'calling ccp: '+api_call_front_str+str(theID)+'/'
                time.sleep(1) # 'be polite' with requests
                api_result = requests.get(api_call_front_str+str(theID)+'/').json()
                theName = api_result['name']
                mail['solarSystemName'] = theName
                #and save this result so we don't call ccp again
                self.solarsystem_lookup[theID] = theName

    def tag_shipTypeID(self):
        for mail in self.history:
            theID = mail['victim']['shipTypeID']
            if mail['victim'].get('shipTypeName', None) != None:
                continue
            #if shipTypeID present in self.ship_lookup don't call the api
            temp_ship_name = self.ship_lookup.get(theID, None)
            if temp_ship_name != None:
                mail['victim']['shipTypeName'] = temp_ship_name
            else: #better call ccp example: https://api.eveonline.com/eve/TypeName.xml.aspx?ids=603
                api_call_front_str = 'https://api.eveonline.com/eve/TypeName.xml.aspx?ids='
                print 'calling ccp: '+ api_call_front_str+str(theID)
                time.sleep(2) # 'be polite' with requests
                api_result = requests.get(api_call_front_str+str(theID)).text
                #since XML parser docs are basically novels to read and they
                #have SECURITY VULNERABILITIES I'm going to not use them, dwi
                #find start of typeName="
                name_start = api_result.find('typeName="') + len('typeName="')
                #find end of the name " />   this means we will correctly fetch names even with double quotes
                name_end_offset = api_result[name_start:].find('" />')
                name_end = name_start + name_end_offset
                theName = api_result[name_start:name_end]
                mail['victim']['shipTypeName'] = theName
                #and save this result so we don't call ccp again
                self.ship_lookup[theID] = theName

    def use_character(self, charid):
        cs = {v:k for k,v in self.character_list.items()}
        charname = cs[charid]
        self.history = [x for x in self.history if charname in x['our_characters'] or charname == x['victim']['characterName']]
        self.characterName = charname

    def write_data_to_file(self):
        print 'writing data'
        with open('out/data/history.json', 'w') as outfile:
            json.dump(self.history, outfile)
        with open('out/data/ship_lookup.json', 'w') as outfile:
            json.dump(self.ship_lookup, outfile)
        with open('out/data/solarsystem_lookup.json', 'w') as outfile:
            json.dump(self.solarsystem_lookup, outfile)

    def update_all(self):
        self.update_kill_history()
        self.prune_unused_history_fields()
        self.tag_as_kill_loss_or_friendly_fire()
        self.tag_involved_characters()
        self.tag_formatted_values()
        self.tag_solarSystemName()
        self.tag_shipTypeID()
        self.write_data_to_file()

    @property
    def data(self):
        result = {'kills':           self.kill_counts('row-kill'),
                  'losses':          self.kill_counts('row-loss'),
                  'history':         self.kills_by_date(),
                  'characters':      sorted(self.character_list.items()),
                  'money_lost':      self.kill_sums('row-loss'),
                  'money_killed':    self.kill_sums('row-kill'),
                  'friendlyfire':    self.kill_counts('row-friendlyfire'),
                  'character_count': len(self.character_list),
                  'characterName':   self.characterName}
        return result

@app.route('/', defaults={'charid': None})
@app.route('/<int:charid>/')
def index(charid):
    print 'character: '+str(charid)
    zKill = zKillAPI()
    if charid:
        zKill.use_character(charid)
    return render_template('index.html', **zKill.data)

@freezer.register_generator
def index():
    print 'main build'
    zKill = zKillAPI()
    zKill.update_all()
    print 'update success'
    yield {'charid': None}
    for x in zKill.character_list.values():
        yield {'charid': x}


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[2] == 'debug':
        logging.basicConfig(level=logging.DEBUG)
    if len(sys.argv) > 1 and sys.argv[1] == 'build':
        freezer.freeze()

    elif len(sys.argv) > 1 and sys.argv[1] == 'forcezkill':
        #keep going until first page is empty
        zKill = zKillAPI()
        while zKill.update_kill_history() != 1:
            time.sleep(10)
        zKill.update_all()
    else:
        app.run(debug=True, host='0.0.0.0')

