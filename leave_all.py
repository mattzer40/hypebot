import asyncio
import discord
from dotenv import load_dotenv
import os
import sys

# Força encoding UTF-8 no output
sys.stdout.reconfigure(encoding='utf-8')

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

client = discord.Client(intents=discord.Intents.default())

@client.event
async def on_ready():
    guilds = list(client.guilds)
    if not guilds:
        print("O bot nao esta em nenhum servidor.")
    else:
        print(f"Bot em {len(guilds)} servidor(es). Saindo de todos...")
        for guild in guilds:
            try:
                await guild.leave()
                print(f"  OK - Saiu de: {guild.name} (ID: {guild.id})")
            except Exception as e:
                print(f"  ERRO ao sair de {guild.name}: {e}")
    print("Concluido!")
    await client.close()

client.run(TOKEN)
