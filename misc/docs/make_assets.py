# -*- coding: utf-8 -*-
"""
USAGE: make_assets.py [-f] [-c]
"""

from __future__ import print_function, unicode_literals
from future.builtins import *  # @UnusedWildImport
from future.moves.urllib import request
import sys
import os
import shutil


CDN_URL = 'http://netdna.bootstrapcdn.com/bootstrap/3.1.1/'

ASSETS = {
    'source/_static/css/base.css': 'https://tests.obspy.org/static/base.css',

    'source/_static/font.css':
        'https://tests.obspy.org/static/font/style.css',
    'source/_static/fonts/icomoon.eot':
        'https://tests.obspy.org/static/font/fonts/icomoon.eot',
    'source/_static/fonts/icomoon.svg':
        'https://tests.obspy.org/static/font/fonts/icomoon.svg',
    'source/_static/fonts/icomoon.ttf':
        'https://tests.obspy.org/static/font/fonts/icomoon.ttf',
    'source/_static/fonts/icomoon.woff':
        'https://tests.obspy.org/static/font/fonts/icomoon.woff',

    'source/_templates/navbar-local.html':
        'https://tests.obspy.org/snippets/navbar.html',
    'source/_templates/footer.html':
        'https://tests.obspy.org/snippets/footer.html',

    'source/_static/css/bootstrap.min.css':
        CDN_URL + 'css/bootstrap.min.css',
    'source/_static/fonts/glyphicons-halflings-regular.eot':
        CDN_URL + 'fonts/glyphicons-halflings-regular.eot',
    'source/_static/fonts/glyphicons-halflings-regular.svg':
        CDN_URL + 'fonts/glyphicons-halflings-regular.svg',
    'source/_static/fonts/glyphicons-halflings-regular.ttf':
        CDN_URL + 'fonts/glyphicons-halflings-regular.ttf',
    'source/_static/fonts/glyphicons-halflings-regular.woff':
        CDN_URL + 'fonts/glyphicons-halflings-regular.woff',
}

force = '-f' in sys.argv
clean = '-c' in sys.argv

if clean:
    print('Cleaning assets ...')
elif force:
    print('Forced downloading assets ...')
else:
    print('Downloading necessary assets ...')

for asset, url in ASSETS.items():
    if clean:
        try:
            print('Deleting %s ...' % (asset))
            os.remove(asset)
        except:
            if force:
                pass
            else:
                raise

    elif force or not os.path.exists(asset):
        print('Downloading %s ...' % (url))
        resp = request.urlopen(url)
        with open(asset, 'wb') as output:
            shutil.copyfileobj(resp, output)
        resp.close()
