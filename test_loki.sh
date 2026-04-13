#!/bin/bash
curl -s -G http://localhost:3100/loki/api/v1/query_range \
  --data-urlencode "query={platform=\"laravel\"}" \
  --data-urlencode "start=$(date -d '-30 days' +%s)000000000" > /tmp/laravel_loki.json
cat /tmp/laravel_loki.json | grep -o 'test laravel' | wc -l
