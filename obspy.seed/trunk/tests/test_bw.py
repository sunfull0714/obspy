# -*- coding: utf-8 -*-

import os

from obspy.seed import SEEDParser

for root,dir,files in os.walk(os.path.join("data", "bw")):
    filelist = [ os.path.join(root,fi) for fi in files]
    for filename in filelist:
        if 'svn' in filename:
            continue
        print filename
        sp = SEEDParser(strict=False)
        try:
            sp.parseSEEDFile(filename)
        except Exception, e:
            sp.debug = True
            sp.parseSEEDFile(filename)
