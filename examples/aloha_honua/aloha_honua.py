#!/usr/bin/env python3
#
# Aiko Service: Aloha Honua
# ~~~~~~~~~~~~~~~~~~~~~~~~~
#
# Usage
# ~~~~~
# LOG_LEVEL=DEBUG registrar &
# LOG_LEVEL=DEBUG ./aloha_honua.py
#
# mosquitto_sub -t '#' -v
#
# NAMESPACE=aiko
# HOST=localhost
# PID=`ps ax | grep python | grep aloha_honua | cut -d" " -f1`
# TOPIC_PATH=$NAMESPACE/$HOST/$PID
# mosquitto_pub -t $TOPIC_PATH/in -m '(test hello)'
#
# To Do
# ~~~~~
# - None, yet !

import click

from aiko_services import *
import aiko_services.framework as aiko
from aiko_services.utilities import get_logger
from aiko_services.utilities.parser import parse
from aiko_services.transport import *

PROTOCOL = "github.com/geekscape/aiko_services/protocol/aloha_honua:0"

_ACTOR_NAME = "AlohaHonua"
_LOGGER = get_logger(__name__)

# --------------------------------------------------------------------------- #

class AlohaHonuaActor(actor.Actor):
    def __init__(self, actor_name):
        super().__init__(actor_name)

    def test(self, value):
        _LOGGER.debug(f"{_ACTOR_NAME}: test({value})")
    #   payload_out = payload_in
    #   aiko.message.publish(aiko.topic_out, payload_out)

    def topic_in_handler(self, aiko, topic, payload_in):
        command, parameters = parse(payload_in)
        _LOGGER.debug(
            f"{_ACTOR_NAME}: topic_in_handler(): {command}:{parameters}"
        )
        self._post_message(actor.Topic.IN, command, parameters)

# --------------------------------------------------------------------------- #

@click.command()
def main():
    actor_name = aiko.public.topic_path
    aloha_honua = AlohaHonuaActor(actor_name)

    aiko.set_protocol(PROTOCOL)
    aiko.add_tags([
        f"class={AlohaHonuaActor.__name__}",  # TODO: Use full class pathname
        f"name={_ACTOR_NAME}"
    ])
    aiko.add_topic_in_handler(aloha_honua.topic_in_handler)
    aiko.process(True)

if __name__ == "__main__":
    main()

# --------------------------------------------------------------------------- #
