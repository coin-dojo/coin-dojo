# spike image: the preview image + python3 (the dispatcher is python). Not shipped, spike only.
FROM coin-dojo-esde-preview:1d3bb7b1
RUN apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends python3 && rm -rf /var/lib/apt/lists/*
