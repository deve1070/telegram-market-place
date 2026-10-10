from aiogram import Bot, Dispatcher
from aiohttp import web

BOT_KEY = web.AppKey("bot", Bot)
DP_KEY = web.AppKey("dp", Dispatcher)
