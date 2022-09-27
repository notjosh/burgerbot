#!/usr/bin/env python3

import json
import logging
import os
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from parser import Parser, Slot
from typing import Any, List

from telegram import ParseMode
from telegram.ext import CommandHandler, Updater
from telegram.ext.callbackcontext import CallbackContext
from telegram.update import Update

CHATS_FILE = "chats.json"
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
TELEGRAM_API_KEY = os.environ.get("TELEGRAM_API_KEY")

if TELEGRAM_API_KEY is None:
    logging.error("TELEGRAM_API_KEY is not set")
    sys.exit(1)

service_map = {
    120335: "Abmeldung einer Wohnung",
    120686: "Anmeldung",
    120701: "Personalausweis beantragen",
    120702: "Meldebescheinigung beantragen",
    120703: "Reisepass beantragen",
    120914: "Zulassung eines Fahrzeuges mit auswärtigem Kennzeichen mit Halterwechsel",
    121469: "Kinderreisepass beantragen / verlängern / aktualisieren",
    121598: "Fahrerlaubnis - Umschreibung einer ausländischen Fahrerlaubnis aus einem EU-/EWR-Staat",
    121627: "Fahrerlaubnis - Ersterteilung beantragen",
    121701: "Beglaubigung von Kopien",
    121921: "Gewerbeanmeldung",
    318998: "Einbürgerung - Verleihung der deutschen Staatsangehörigkeit beantragen",
    324280: "Niederlassungserlaubnis oder Erlaubnis",
    326798: "Blaue Karte EU auf einen neuen Pass übertragen",
    327537: "Fahrerlaubnis - Umschreibung einer ausländischen",
}


@dataclass
class Message:
    message: str
    ts: int  # timestamp of adding msg to cache in seconds


@dataclass
class User:
    chat_id: int
    services: List[int]

    def __init__(self, chat_id, services=[]) -> None:
        self.chat_id = chat_id
        self.services = services

    def marshall_user(self) -> dict[str, Any]:
        self.services = list(
            set([s for s in self.services if s in list(service_map.keys())])
        )
        return asdict(self)


class Bot:
    def __init__(self) -> None:
        self.updater = Updater(TELEGRAM_API_KEY)
        self.__init_chats()
        self.users = self.__get_chats()
        self.services = self.__get_uq_services()
        self.parser = Parser(self.services)
        self.dispatcher = self.updater.dispatcher
        self.dispatcher.add_handler(CommandHandler("help", self.__help))
        self.dispatcher.add_handler(CommandHandler("start", self.__start))
        self.dispatcher.add_handler(CommandHandler("stop", self.__stop))
        self.dispatcher.add_handler(CommandHandler("add_service", self.__add_service))
        self.dispatcher.add_handler(
            CommandHandler("remove_service", self.__remove_service)
        )
        self.dispatcher.add_handler(CommandHandler("my_services", self.__my_services))
        self.dispatcher.add_handler(CommandHandler("services", self.__services))
        self.cache: List[Message] = []

    def __get_uq_services(self) -> List[int]:
        services: List[int] = []
        for u in self.users:
            services.extend(u.services)
        services = list(filter(lambda x: x in service_map.keys(), services))
        return list(set(services))

    def __init_chats(self) -> None:
        if not os.path.exists(CHATS_FILE):
            with open(CHATS_FILE, "w") as f:
                f.write("[]")

    def __get_chats(self) -> List[User]:
        with open(CHATS_FILE, "r") as f:
            users = [User(u["chat_id"], u["services"]) for u in json.load(f)]
            f.close()
            print(users)
            return users

    def __persist_chats(self) -> None:
        with open(CHATS_FILE, "w") as f:
            json.dump([u.marshall_user() for u in self.users], f)
            f.close()

    def __add_chat(self, chat_id: int) -> None:
        if chat_id not in [u.chat_id for u in self.users]:
            logging.info("adding new user")
            self.users.append(User(chat_id))
            self.__persist_chats()

    def __remove_chat(self, chat_id: int) -> None:
        logging.info("removing the chat " + str(chat_id))
        self.users = [u for u in self.users if u.chat_id != chat_id]
        self.__persist_chats()

    def __services(self, update: Update, _: CallbackContext) -> None:
        if update.message is None:
            logging.info("update.message is None, bailing early")
            return

        services_text = ""
        for k, v in service_map.items():
            services_text += f"{k} - {v}\n"
        update.message.reply_text("Available services:\n" + services_text)

    def __help(self, update: Update, _: CallbackContext) -> None:
        if update.message is None:
            logging.info("update.message is None, bailing early")
            return

        try:
            update.message.reply_text(
                """
/start - start the bot
/stop - stop the bot
/add_service <service_id> - add service to your list
/remove_service <service_id> - remove service from your list
/my_services - view services on your list
/services - list of available services
"""
            )
        except Exception as e:
            logging.error(e)

    def __start(self, update: Update, _: CallbackContext) -> None:
        if update.message is None:
            logging.info("update.message is None, bailing early")
            return

        self.__add_chat(update.message.chat_id)
        logging.info(f"got new user with id {update.message.chat_id}")
        update.message.reply_text(
            "Welcome to BurgerBot. When there will be slot - you will receive notification. To get information about usage - type /help. To stop it - just type /stop"
        )

    def __stop(self, update: Update, _: CallbackContext) -> None:
        if update.message is None:
            logging.info("update.message is None, bailing early")
            return

        self.__remove_chat(update.message.chat_id)
        update.message.reply_text("Thanks for using me! Bye!")

    def __my_services(self, update: Update, _: CallbackContext) -> None:
        if update.message is None:
            logging.info("update.message is None, bailing early")
            return

        try:
            service_ids = set(
                service_id
                for u in self.users
                for service_id in u.services
                if u.chat_id == update.message.chat_id
            )
            msg = (
                "\n".join([f" - {service_id}" for service_id in service_ids])
                or " - (none)"
            )
            update.message.reply_text(
                "The following services are on your list:\n" + msg
            )
        except Exception as e:
            logging.error(e)

    def __add_service(self, update: Update, _: CallbackContext) -> None:
        if update.message is None:
            logging.info("update.message is None, bailing early")
            return

        if update.message.text is None:
            logging.info("update.message.text is None, bailing early")
            return

        logging.info(f"adding service {update.message}")
        try:
            service_id = int(update.message.text.split(" ")[1])
            for u in self.users:
                if u.chat_id == update.message.chat_id:
                    u.services.append(int(service_id))
                    self.__persist_chats()
                    break
            update.message.reply_text("Service added")
        except Exception as e:
            update.message.reply_text(
                "Failed to add service, have you specified the service id?"
            )
            logging.error(e)

    def __remove_service(self, update: Update, _: CallbackContext) -> None:
        if update.message is None:
            logging.info("update.message is None, bailing early")
            return

        if update.message.text is None:
            logging.info("update.message.text is None, bailing early")
            return

        logging.info(f"removing service {update.message}")
        try:
            service_id = int(update.message.text.split(" ")[1])
            for u in self.users:
                if u.chat_id == update.message.chat_id:
                    u.services.remove(int(service_id))
                    self.__persist_chats()
                    break
            update.message.reply_text("Service removed")
        except IndexError:
            update.message.reply_text(
                "Wrong usage. Please type '/remove_service 123456'"
            )

    def __poll(self) -> None:
        try:
            self.updater.start_polling()
        except Exception as e:
            logging.warn(e)
            logging.warn("got error during polling, retying")
            return self.__poll()

    def __parse(self) -> None:
        while True:
            logging.debug("starting parse run")
            slots = self.parser.parse()
            for slot in slots:
                self.__send_message(slot)
            time.sleep(30)

    def __send_message(self, slot: Slot) -> None:
        if self.__msg_in_cache(slot.result.url):
            logging.info("Notification is cached already. Do not repeat sending")
            return
        self.__add_msg_to_cache(slot.result.url)
        md_msg = f"There are slots on {self.__date_from_msg(slot.result.date)} available for booking for {service_map[slot.service.id]}, click [here]({slot.service.url}) to check it out"
        users = [u for u in self.users if slot.service.id in u.services]
        for u in users:
            logging.debug(f"sending msg to {str(u.chat_id)}")
            try:
                self.updater.bot.send_message(
                    chat_id=u.chat_id, text=md_msg, parse_mode=ParseMode.MARKDOWN_V2
                )
            except Exception as e:
                if (
                    "bot was blocked by the user" in e.__str__()
                    or "user is deactivated" in e.__str__()
                ):
                    logging.info(
                        "removing since user blocked bot or user was deactivated"
                    )
                    self.__remove_chat(u.chat_id)
                else:
                    logging.warning(e)
        self.__clear_cache()

    def __msg_in_cache(self, msg: str) -> bool:
        for m in self.cache:
            if m.message == msg:
                return True
        return False

    def __add_msg_to_cache(self, msg: str) -> None:
        self.cache.append(Message(msg, int(time.time())))

    def __clear_cache(self) -> None:
        cur_ts = int(time.time())
        if len(self.cache) > 0:
            logging.info("clearing some messages from cache")
            self.cache = [m for m in self.cache if (cur_ts - m.ts) < 300]

    def __date_from_msg(self, date: datetime) -> str:
        return date.strftime("%d %B")

    def start(self) -> None:
        logging.info("starting bot")
        poll_task = threading.Thread(target=self.__poll)
        parse_task = threading.Thread(target=self.__parse)
        parse_task.start()
        poll_task.start()
        parse_task.join()
        poll_task.join()


def main() -> None:
    bot = Bot()
    bot.start()


if __name__ == "__main__":
    log_level = LOG_LEVEL
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)-5.5s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    main()
