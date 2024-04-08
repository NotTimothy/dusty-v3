from bot import MusicBot
import nest_asyncio
import asyncio

async def main():
    bot = MusicBot()
    await bot.run()

if __name__ == "__main__":
    nest_asyncio.apply()
    asyncio.run(main())