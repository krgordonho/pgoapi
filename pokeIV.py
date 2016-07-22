#!/usr/bin/env python
"""
pgoapi - Pokemon Go API
Copyright (c) 2016 tjado <https://github.com/tejado>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE
OR OTHER DEALINGS IN THE SOFTWARE.

Author: tjado <https://github.com/tejado>
"""

import os
import re
import sys
import json
import time
import struct
import pprint
import logging
import requests
import argparse
import getpass
import csv
import time

# add directory of this file to PATH, so that the package will be found
sys.path.append(os.path.dirname(os.path.realpath(__file__)))

# import Pokemon Go API lib
from pgoapi import pgoapi
from pgoapi import utilities as util

# other stuff
from google.protobuf.internal import encoder
from geopy.geocoders import GoogleV3
from s2sphere import Cell, CellId, LatLng

log = logging.getLogger(__name__)

def get_pos_by_name(location_name):
    geolocator = GoogleV3()
    loc = geolocator.geocode(location_name, timeout=10)

    log.info('Your given location: %s', loc.address.encode('utf-8'))
    log.info('lat/long/alt: %s %s %s', loc.latitude, loc.longitude, loc.altitude)

    return (loc.latitude, loc.longitude, loc.altitude)

def get_cell_ids(lat, long, radius = 10):
    origin = CellId.from_lat_lng(LatLng.from_degrees(lat, long)).parent(15)
    walk = [origin.id()]
    right = origin.next()
    left = origin.prev()

    # Search around provided radius
    for i in range(radius):
        walk.append(right.id())
        walk.append(left.id())
        right = right.next()
        left = left.prev()

    # Return everything
    return sorted(walk)

def encode(cellid):
    output = []
    encoder._VarintEncoder()(output.append, cellid)
    return ''.join(output)

def init_config():
    parser = argparse.ArgumentParser()
    config_file = "config.json"

    # If config file exists, load variables from json
    load   = {}
    if os.path.isfile(config_file):
        with open(config_file) as data:
            load.update(json.load(data))

    # Read passed in Arguments
    required = lambda x: not x in load
    parser.add_argument("-a", "--auth_service", help="Auth Service ('ptc' or 'google')",required=required("auth_service"))
    parser.add_argument("-u", "--username", help="Username", required=required("username"))
    parser.add_argument("-p", "--password", help="Password")
    parser.add_argument("-c", "--clean", help="Transfers all but the highest of each pokemon (see -ivmin)", action="store_true")
    parser.add_argument("-m", "--min", help="All pokemon equal to or above this IV value are kept regardless of duplicates")
    parser.set_defaults(DEBUG=False, TEST=False)
    config = parser.parse_args()

    # Passed in arguments shoud trump
    for key in config.__dict__:
        if key in load and config.__dict__[key] == None:
            config.__dict__[key] = str(load[key])

    if config.__dict__["password"] is None:
        log.info("Secure Password Input (if there is no password prompt, use --password <pw>):")
        config.__dict__["password"] = getpass.getpass()

    if config.auth_service not in ['ptc', 'google']:
      log.error("Invalid Auth service specified! ('ptc' or 'google')")
      return None

    return config
	
def main():
    # log settings
    # log format
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s [%(module)10s] [%(levelname)5s] %(message)s')
    # log level for http request class
    logging.getLogger("requests").setLevel(logging.WARNING)
    # log level for main pgoapi class
    logging.getLogger("pgoapi").setLevel(logging.INFO)
    # log level for internal pgoapi class
    logging.getLogger("rpc_api").setLevel(logging.INFO)

    config = init_config()
    if not config:
        return
    
    # instantiate pgoapi
    api = pgoapi.PGoApi()
    if not api.login(config.auth_service, config.username, config.password):
        return
    # get inventory call
    # ----------------------
    api.get_inventory()
    # execute the RPC call to get all pokemon and their stats
    response_dict = api.call()
    # all pokemon_data entries
    pokemon = get_pokemon(response_dict)
    if len(pokemon) == 0:
		print('You have no pokemon...')
		return
    # highest IV pokemon
    best = get_best_pokemon(pokemon, config.min if config.min is not None else 101)
    # rest of pokemon
    extras = list(set(pokemon) - set(best))
    
    print('------------Highest IV pokemon------------')
    print('{0:<20} {1:<15} {2:<15}'.format('[pokemon]','[cp]','[iv]'))
    for p in best:
        print('{0:<20} {1:<15} {2:<8.2%}'.format(str(p.name),str(p.cp),p.ivPercent))
    print('------------To be transfered------------')
    for p in extras:
        print('{0:<20} {1:<15} {2:<8.2%}'.format(str(p.name),str(p.cp),p.ivPercent))
        
    # release extras
    if config.clean:
        for p in extras:
            print('{0:<30} {1:<15} {2:<8.2%}'.format('removing pokemon: '+str(p.name),str(p.cp),p.ivPercent))
            api.release_pokemon(pokemon_id = p.id)
            api.call()
            time.sleep(10)

def get_pokemon(response_dict):
    data = []
    
    with open('names.tsv') as f:
        f.readline()
        names = dict(csv.reader(f, delimiter='\t'))
    
    def _add_node(node):
        pok = type('',(),{})
        pok.id = node["id"]
        pok.name = names[str(node["pokemon_id"])]
        pok.number = node["pokemon_id"]
        pok.stamina = node["individual_stamina"] if "individual_stamina" in node else 0
        pok.attack = node["individual_attack"] if "individual_attack" in node else 0
        pok.defense = node["individual_defense"] if "individual_defense" in node else 0
        pok.iv = ((pok.stamina + pok.attack + pok.defense) / float(45))*100
        pok.ivPercent = pok.iv/100
        pok.cp = node["cp"]
        data.append(pok)
	
    def _find_node(node):
        try: _add_node(node["pokemon_data"])
        except KeyError: pass
        return node
    
    json.loads(json.dumps(response_dict), object_hook=_find_node)
    return data

def get_best_pokemon(pokemon, ivmin):
    if len(pokemon) == 0:
        return []
    
    best = []
    
    #sort by iv
    pokemon.sort(key=lambda x: x.iv, reverse=True)
    for p in pokemon:
        #if there isn't a pokemon in best with the same number (name) as this one, add it
        if not any(x.number == p.number for x in best):
            best.append(p)
        #if it passes the minimum iv test
        elif p.iv >= float(ivmin):			
            best.append(p)

    return best

if __name__ == '__main__':
    main()

		
	