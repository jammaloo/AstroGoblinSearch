#!/bin/bash
git add .
git commit -m "New Transcriptions"
git push
ssh search.astrogoblin.jammaloo.com 'cd /var/www/jammaloo/subdomains/search.astrogoblin.jammaloo.com/private && ./update.sh'
