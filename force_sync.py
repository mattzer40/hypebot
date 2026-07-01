import asyncio, discord, json, os, sys

TOKEN_FILE = r'C:\Users\Anderson Chaves\Downloads\bot\token.txt'
SETTINGS_FILE = r'C:\Users\Anderson Chaves\Downloads\bot\settings.json'

with open(TOKEN_FILE, 'r') as f:
    TOKEN = f.read().strip()

intents = discord.Intents.default()
bot = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(bot)

@bot.event
async def on_ready():
    print(f'Conectado como {bot.user}')
    for guild in bot.guilds:
        try:
            bot.tree.clear_commands(guild=guild)
            await bot.tree.sync(guild=guild)
            print(f'Sync em {guild.name}')
        except Exception as e:
            print(f'Erro em {guild.name}: {e}')
    await bot.close()

bot.run(TOKEN)
