import asyncio
import datetime as dt
import enum
import random
import re
import typing as t
from enum import Enum

import aiohttp
import discord
import wavelink
from discord.ext import commands


# TODO: In the refactored code:
# The Queue class remains mostly the same, but now each server will have its own instance of the queue.
# The Music cog now uses dictionaries (self.queues, self.voice_clients, self.players) to store the queue, voice client, and player for each server, using the guild ID as the key.
# The get_queue(), get_voice_client(), and get_player() methods are added to retrieve the respective instances for a given guild ID, creating new instances if they don't exist.
# The start_playback(), advance(), repeat_track(), and add_tracks() methods are updated to work with the guild-specific queue, voice client, and player.
# The rest of the commands and methods in the Music cog should be updated to use the new queue and player dictionaries, passing the guild_id as an argument where necessary.
# With these changes, the music bot will be able to handle multiple servers simultaneously, each with its own queue and playback.
# Note: Make sure to update the other commands and methods in the Music cog to work with the new queue and player dictionaries, similar to how add_tracks() is updated in the provided code.


URL_REGEX = r"(?i)\b((?:https?://|www\d{0,3}[.]|[a-z0-9.\-]+[.][a-z]{2,4}/)(?:[^\s()<>]+|\(([^\s()<>]+|(\([^\s()<>]+\)))*\))+(?:\(([^\s()<>]+|(\([^\s()<>]+\)))*\)|[^\s`!()\[\]{};:'\".,<>?«»“”‘’]))"
LYRICS_URL = "https://some-random-api.ml/lyrics?title="
HZ_BANDS = (20, 40, 63, 100, 150, 250, 400, 450, 630, 1000, 1600, 2500, 4000, 10000, 16000)
TIME_REGEX = r"([0-9]{1,2})[:ms](([0-9]{1,2})s?)?"
OPTIONS = {
    "1️⃣": 0,
    "2⃣": 1,
    "3⃣": 2,
    "4⃣": 3,
    "5⃣": 4,
}


class AlreadyConnectedToChannel(commands.CommandError):
    pass


class NoVoiceChannel(commands.CommandError):
    pass


class QueueIsEmpty(commands.CommandError):
    pass


class NoTracksFound(commands.CommandError):
    pass


class PlayerIsAlreadyPaused(commands.CommandError):
    pass


class NoMoreTracks(commands.CommandError):
    pass


class NoPreviousTracks(commands.CommandError):
    pass


class InvalidRepeatMode(commands.CommandError):
    pass


class VolumeTooLow(commands.CommandError):
    pass


class VolumeTooHigh(commands.CommandError):
    pass


class MaxVolume(commands.CommandError):
    pass


class MinVolume(commands.CommandError):
    pass


class NoLyricsFound(commands.CommandError):
    pass


class InvalidEQPreset(commands.CommandError):
    pass


class NonExistentEQBand(commands.CommandError):
    pass


class EQGainOutOfBounds(commands.CommandError):
    pass


class InvalidTimeString(commands.CommandError):
    pass

class MissingRequiredArgument(commands.CommandError):
    pass

class RepeatMode(Enum):
    NONE = 0
    ONE = 1
    ALL = 2


class Queue:
    def __init__(self):
        self._queue = []
        self.position = 0
        self.repeat_mode = RepeatMode.NONE

    @property
    def is_empty(self):
        return not self._queue

    @property
    def current_track(self):
        if not self._queue:
            raise QueueIsEmpty

        if self.position <= len(self._queue) - 1:
            return self._queue[self.position]

    @property
    def upcoming(self):
        if not self._queue:
            raise QueueIsEmpty

        return self._queue[self.position + 1:]

    @property
    def history(self):
        if not self._queue:
            raise QueueIsEmpty

        return self._queue[:self.position]

    @property
    def length(self):
        return len(self._queue)

    def add(self, *args):
        self._queue.extend(args)

    def get_next_track(self):
        if not self._queue:
            raise QueueIsEmpty

        self.position += 1

        if self.position < 0:
            return None
        elif self.position > len(self._queue) - 1:
            if self.repeat_mode == RepeatMode.ALL:
                self.position = 0
            else:
                return None

        return self._queue[self.position]

    def shuffle(self):
        if not self._queue:
            raise QueueIsEmpty

        upcoming = self.upcoming
        random.shuffle(upcoming)
        self._queue = self._queue[:self.position + 1]
        self._queue.extend(upcoming)

    def set_repeat_mode(self, mode):
        if mode == "none":
            self.repeat_mode = RepeatMode.NONE
        elif mode == "1":
            self.repeat_mode = RepeatMode.ONE
        elif mode == "all":
            self.repeat_mode = RepeatMode.ALL

    def empty(self):
        self._queue.clear()
        self.position = 0

class Music(commands.Cog):
    def __init__(self, bot, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bot = bot
        self.queues = {}
        self.voice_clients = {}
        self.players = {}
        self.eq_levels = [0.] * 15

    async def get_queue(self, guild_id):
        if guild_id not in self.queues:
            self.queues[guild_id] = Queue()
        return self.queues[guild_id]

    async def get_voice_client(self, guild_id):
        if guild_id not in self.voice_clients:
            self.voice_clients[guild_id] = None
        return self.voice_clients[guild_id]

    async def get_player(self, guild_id):
        if guild_id not in self.players:
            self.players[guild_id] = None
        return self.players[guild_id]

    async def start_playback(self, guild_id):
        queue = await self.get_queue(guild_id)
        voice_client = await self.get_voice_client(guild_id)
        if voice_client and not voice_client.is_playing():
            await voice_client.play(queue.current_track)

    async def advance(self, guild_id):
        queue = await self.get_queue(guild_id)
        voice_client = await self.get_voice_client(guild_id)
        try:
            if (track := queue.get_next_track()) is not None:
                await voice_client.play(track)
        except QueueIsEmpty:
            pass

    async def repeat_track(self, guild_id):
        queue = await self.get_queue(guild_id)
        voice_client = await self.get_voice_client(guild_id)
        await voice_client.play(queue.current_track)

    async def add_tracks(self, ctx, tracks):
        guild_id = ctx.guild.id
        queue = await self.get_queue(guild_id)
        voice_client = await self.get_voice_client(guild_id)
        player = await self.get_player(guild_id)

        if not tracks:
            raise NoTracksFound
        elif len(tracks) == 1:
            queue.add(tracks[0])
            await ctx.send(f"Added {tracks[0].title} to the queue.")
        else:
            if (track := await self.choose_track(ctx, tracks)) is not None:
                queue.add(track)
                await ctx.send(f"Added {track.title} to the queue.")

        if not player.is_playing() and not queue.is_empty():
            await self.start_playback(guild_id)

    async def choose_track(self, ctx, tracks):
        def _check(r, u):
            return (
                r.emoji in OPTIONS.keys()
                and u == ctx.author
                and r.message.id == msg.id
            )

        embed = discord.Embed(
            title="Choose a song",
            description=(
                "\n".join(
                    f"**{i+1}.** {t.title} ({t.length//60000}:{str(t.length%60).zfill(2)})"
                    for i, t in enumerate(tracks[:5])
                )
            ),
            colour=ctx.author.colour,
            timestamp=dt.datetime.utcnow()
        )
        embed.set_author(name="Query Results")
        embed.set_footer(text=f"Invoked by {ctx.author.display_name}", icon_url=ctx.author.avatar)

        msg = await ctx.send(embed=embed)
        for emoji in list(OPTIONS.keys())[:min(len(tracks), len(OPTIONS))]:
            await msg.add_reaction(emoji)

        try:
            reaction, _ = await self.bot.wait_for("reaction_add", timeout=60.0, check=_check)
        except asyncio.TimeoutError:
            await msg.delete()
            await ctx.message.delete()
        else:
            await msg.delete()
            return tracks[OPTIONS[reaction.emoji]]
        
    @commands.command(name="yt", alias=["youtube"])
    async def play_youtube_command(self, ctx, *, query: t.Optional[str]):
        """Play YouTube song `!yt truck got stuck` `!yt https://www.youtube.com/watch?v=4WAxMI1QJMQ`"""

        if not ctx.voice_client:
            vc: wavelink.Player = await ctx.author.voice.channel.connect(cls=wavelink.Player)
        else:
            vc: wavelink.Player = ctx.voice_client
            
        if query is None:
            if self.queue.is_empty:
                raise QueueIsEmpty

            await ctx.send("Playback resumed.")

        else:
            query = query.strip("<>")
            if not re.match(URL_REGEX, query):
                query = f"{query}"
                
        node = wavelink.Pool.get_node()
        player = node.get_player(ctx.guild.id)

        tracks = await wavelink.Playable.search(query)
        await self.add_tracks(ctx, tracks, vc, player)
        
        await vc.play(track)
        
    @play_youtube_command.error
    async def play_youtube_command_error(self, ctx, exc):
        if isinstance(exc, QueueIsEmpty):
            await ctx.send("No songs to play as the queue is empty.")
        elif isinstance(exc, NoVoiceChannel):
            await ctx.send("No suitable voice channel was provided.")    

    @commands.command(name="sc", alias=["soundcloud", "sound", "cloud"])
    async def play_sound_cloud_command(self, ctx, *, query: t.Optional[str]):
        """Play SoundCloud song `!sc https://soundcloud.com/superstar-pride/painting-pictures`"""
        if not ctx.voice_client:
            vc: wavelink.Player = await ctx.author.voice.channel.connect(cls=wavelink.Player)
        else:
            vc: wavelink.Player = ctx.voice_client
        
        if query is None:
            if self.queue.is_empty:
                raise QueueIsEmpty
            
            await ctx.send("Playback resumed.")

        else:
            query = query.strip("<>")
            if not re.match(URL_REGEX, query):
                query = f"{query}"
                
        node = wavelink.Pool.get_node()
        player = node.get_player(ctx.guild.id)

        tracks = await wavelink.Playable.search(query)
        
        await self.add_tracks(ctx, tracks, vc, player)
        
    @play_sound_cloud_command.error
    async def play_sound_cloud_command_error(self, ctx, exc):
        if isinstance(exc, QueueIsEmpty):
            await ctx.send("No songs to play as the queue is empty.")
        elif isinstance(exc, NoVoiceChannel):
            await ctx.send("No suitable voice channel was provided.")    

    @commands.command(name="pause")
    async def pause_command(self, ctx):
        node = wavelink.Pool.get_node()
        player = node.get_player(ctx.guild.id)

        if not player.is_paused:
            raise PlayerIsAlreadyPaused

        await player.pause()
        await ctx.send("Playback paused.")

    @pause_command.error
    async def pause_command_error(self, ctx, exc):
        if isinstance(exc, PlayerIsAlreadyPaused):
            await ctx.send("Already paused.")
            
    @commands.command(name="resume")
    async def resume_command(self, ctx):
        """Resume song."""
        node = wavelink.Pool.get_node()
        player = node.get_player(ctx.guild.id)

        await player.resume()
        
        await ctx.send("Playback resumed.")

    @commands.command(name="stop")
    async def stop_command(self, ctx):
        """Stop playing song.""" 
        node = wavelink.Pool.get_node()
        player = node.get_player(ctx.guild.id)
        self.queue.empty()
        await player.stop()
        await ctx.send("Playback stopped.")

    @commands.command(name="next", aliases=["skip"])
    async def next_command(self, ctx):
        node = wavelink.Pool.get_node()
        player = node.get_player(ctx.guild.id)

        if not self.queue.upcoming:
            await player.stop()
            raise NoMoreTracks

        await player.stop()
        await ctx.send("Playing next track in queue.")

        new_track = self.queue.get_next_track()
        await player.play(new_track)

    @next_command.error
    async def next_command_error(self, ctx, exc):
        if isinstance(exc, QueueIsEmpty):
            await ctx.send("This could not be executed as the queue is currently empty.")
        elif isinstance(exc, NoMoreTracks):
            await ctx.send("There are no more tracks in the queue.")

    @commands.command(name="previous")
    async def previous_command(self, ctx):
        """Play previous song."""
        node = wavelink.Pool.get_node()
        player = node.get_player(ctx.guild.id)

        if not self.queue.history:
            await player.stop()
            raise NoPreviousTracks

        self.queue.position -= 1
        await player.stop()
        await ctx.send("Playing previous track in queue.")
        
        await player.play(self.queue.current_track)

    @previous_command.error
    async def previous_command_error(self, ctx, exc):
        if isinstance(exc, QueueIsEmpty):
            await ctx.send("This could not be executed as the queue is currently empty.")
        elif isinstance(exc, NoPreviousTracks):
            await ctx.send("There are no previous tracks in the queue.")

    @commands.command(name="shuffle")
    async def shuffle_command(self, ctx):
        """Suffle songs."""
        node = wavelink.Pool.get_node()
        player = node.get_player(ctx.guild.id)
        self.queue.shuffle()
        await ctx.send("Queue shuffled.")

    @shuffle_command.error
    async def shuffle_command_error(self, ctx, exc):
        if isinstance(exc, QueueIsEmpty):
            await ctx.send("The queue could not be shuffled as it is currently empty.")

    @commands.command(name="repeat")
    async def repeat_command(self, ctx, mode: str):
        """Repeat song. `!repeat all` `!repeat 1` `!repeat none`"""
        if mode is None:
            raise MissingRequiredArgument
        
        if mode not in ("none", "1", "all"):
            raise InvalidRepeatMode

        node = wavelink.Pool.get_node()
        player = node.get_player(ctx.guild.id)
        self.queue.set_repeat_mode(mode)
        await ctx.send(f"The repeat mode has been set to {mode}.")
        
    @repeat_command.error
    async def repeat_command_error(self, ctx, exc):
        if isinstance(exc, InvalidRepeatMode):
           await ctx.send(f"Not a valid repeat mode. Options are: ['none', '1', 'all']") 
        if isinstance(exc, commands.MissingRequiredArgument):
            await ctx.send(f"Please provide a repeat mode.  Options are: ['none', '1', 'all']")

    @commands.command(name="queue")
    async def queue_command(self, ctx, show: t.Optional[int] = 10):
        """Show the queue"""
        if self.queue.is_empty:
            raise QueueIsEmpty

        embed = discord.Embed(
            title="Queue",
            description=f"Showing up to next {show} tracks",
            colour=ctx.author.colour,
            timestamp=dt.datetime.utcnow()
        )
        embed.set_author(name="Query Results")
        embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.avatar)
        embed.add_field(
            name="Currently playing",
            value=getattr(self.queue.current_track, "title", "No tracks currently playing."),
            inline=False
        )
        if upcoming := self.queue.upcoming:
            embed.add_field(
                name="Next up",
                value="\n".join(t.title for t in upcoming[:show]),
                inline=False
            )

        msg = await ctx.send(embed=embed)

    @queue_command.error
    async def queue_command_error(self, ctx, exc):
        if isinstance(exc, QueueIsEmpty):
            await ctx.send("The queue is currently empty.")

    # Requests -----------------------------------------------------------------

    @commands.group(name="volume", invoke_without_command=True)
    async def volume_group(self, ctx, volume: int):
        node = wavelink.Pool.get_node()
        player = node.get_player(ctx.guild.id)

        if volume < 0:
            raise VolumeTooLow

        if volume > 150:
            raise VolumeTooHigh

        await player.set_volume(volume)
        await ctx.send(f"Volume set to {volume:,}%")

    @volume_group.error
    async def volume_group_error(self, ctx, exc):
        if isinstance(exc, VolumeTooLow):
            await ctx.send("The volume must be 0% or above.")
        elif isinstance(exc, VolumeTooHigh):
            await ctx.send("The volume must be 150% or below.")

    @volume_group.command(name="up")
    async def volume_up_command(self, ctx):
        node = wavelink.Pool.get_node()
        player = node.get_player(ctx.guild.id)

        if player.volume == 150:
            raise MaxVolume

        await player.set_volume(value := min(player.volume + 10, 150))
        await ctx.send(f"Volume set to {value:,}%")

    @volume_up_command.error
    async def volume_up_command_error(self, ctx, exc):
        if isinstance(exc, MaxVolume):
            await ctx.send("The player is already at max volume.")

    @volume_group.command(name="down")
    async def volume_down_command(self, ctx):
        node = wavelink.Pool.get_node()
        player = node.get_player(ctx.guild.id)

        if player.volume == 0:
            raise MinVolume

        await player.set_volume(value := max(0, player.volume - 10))
        await ctx.send(f"Volume set to {value:,}%")

    @volume_down_command.error
    async def volume_down_command_error(self, ctx, exc):
        if isinstance(exc, MinVolume):
            await ctx.send("The player is already at min volume.")

    @commands.command(name="lyrics")
    async def lyrics_command(self, ctx, name: t.Optional[str]):
        node = wavelink.Pool.get_node()
        player = node.get_player(ctx.guild.id)
        name = name or self.queue.current_track.title

        async with ctx.typing():
            async with aiohttp.request("GET", LYRICS_URL + name, headers={}) as r:
                if not 200 <= r.status <= 299:
                    raise NoLyricsFound

                data = await r.json()

                if len(data["lyrics"]) > 2000:
                    return await ctx.send(f"<{data['links']['genius']}>")

                embed = discord.Embed(
                    title=data["title"],
                    description=data["lyrics"],
                    colour=ctx.author.colour,
                    timestamp=dt.datetime.utcnow(),
                )
                embed.set_thumbnail(url=data["thumbnail"]["genius"])
                embed.set_author(name=data["author"])
                await ctx.send(embed=embed)

    @lyrics_command.error
    async def lyrics_command_error(self, ctx, exc):
        if isinstance(exc, NoLyricsFound):
            await ctx.send("No lyrics could be found.")

    @commands.command(name="eq")
    async def eq_command(self, ctx, preset: str):
        node = wavelink.Pool.get_node()
        player = node.get_player(ctx.guild.id)

        eq = getattr(wavelink.eqs.Equalizer, preset, None)
        if not eq:
            raise InvalidEQPreset

        await player.set_eq(eq())
        await ctx.send(f"Equaliser adjusted to the {preset} preset.")

    @eq_command.error
    async def eq_command_error(self, ctx, exc):
        if isinstance(exc, InvalidEQPreset):
            await ctx.send("The EQ preset must be either 'flat', 'boost', 'metal', or 'piano'.")

    @commands.command(name="adveq", aliases=["aeq"])
    async def adveq_command(self, ctx, band: int, gain: float):
        node = wavelink.Pool.get_node()
        player = node.get_player(ctx.guild.id)

        if not 1 <= band <= 15 and band not in HZ_BANDS:
            raise NonExistentEQBand

        if band > 15:
            band = HZ_BANDS.index(band) + 1

        if abs(gain) > 10:
            raise EQGainOutOfBounds

        player.eq_levels[band - 1] = gain / 10
        eq = wavelink.eqs.Equalizer(levels=[(i, gain) for i, gain in enumerate(player.eq_levels)])
        await player.set_eq(eq)
        await ctx.send("Equaliser adjusted.")

    @adveq_command.error
    async def adveq_command_error(self, ctx, exc):
        if isinstance(exc, NonExistentEQBand):
            await ctx.send(
                "This is a 15 band equaliser -- the band number should be between 1 and 15, or one of the following "
                "frequencies: " + ", ".join(str(b) for b in HZ_BANDS)
            )
        elif isinstance(exc, EQGainOutOfBounds):
            await ctx.send("The EQ gain for any band should be between 10 dB and -10 dB.")

    @commands.command(name="playing", aliases=["np"])
    async def playing_command(self, ctx):
        """Shows current playing song."""
        node = wavelink.Pool.get_node()
        player = node.get_player(ctx.guild.id)

        if not player.playing:
            raise PlayerIsAlreadyPaused

        embed = discord.Embed(
            title="Now playing",
            colour=ctx.author.colour,
            timestamp=dt.datetime.utcnow(),
        )
        embed.set_author(name="Playback Information")
        embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.avatar)
        embed.add_field(name="Track title", value=self.queue.current_track.title, inline=False)
        embed.add_field(name="Artist", value=self.queue.current_track.author, inline=False)

        position = divmod(player.position, 60000)
        length = divmod(self.queue.current_track.length, 60000)
        embed.add_field(
            name="Position",
            value=f"{int(position[0])}:{round(position[1]/1000):02}/{int(length[0])}:{round(length[1]/1000):02}",
            inline=False
        )

        await ctx.send(embed=embed)

    @playing_command.error
    async def playing_command_error(self, ctx, exc):
        if isinstance(exc, PlayerIsAlreadyPaused):
            await ctx.send("There is no track currently playing.")

    @commands.command(name="skipto", aliases=["playindex"])
    async def skipto_command(self, ctx, index: int):
        node = wavelink.Pool.get_node()
        player = node.get_player(ctx.guild.id)

        if self.queue.is_empty:
            raise QueueIsEmpty

        if not 0 <= index <= self.queue.length:
            raise NoMoreTracks

        self.queue.position = index - 2
        await player.stop()
        await ctx.send(f"Playing track in position {index}.")

    @skipto_command.error
    async def skipto_command_error(self, ctx, exc):
        if isinstance(exc, QueueIsEmpty):
            await ctx.send("There are no tracks in the queue.")
        elif isinstance(exc, NoMoreTracks):
            await ctx.send("That index is out of the bounds of the queue.")

    @commands.command(name="restart")
    async def restart_command(self, ctx):
        node = wavelink.Pool.get_node()
        player = node.get_player(ctx.guild.id)

        if self.queue.is_empty:
            raise QueueIsEmpty

        await player.seek(0)
        await ctx.send("Track restarted.")

    @restart_command.error
    async def restart_command_error(self, ctx, exc):
        if isinstance(exc, QueueIsEmpty):
            await ctx.send("There are no tracks in the queue.")

    @commands.command(name="seek")
    async def seek_command(self, ctx, position: str):
        node = wavelink.Pool.get_node()
        player = node.get_player(ctx.guild.id)

        if self.queue.is_empty:
            raise QueueIsEmpty

        if not (match := re.match(TIME_REGEX, position)):
            raise InvalidTimeString

        if match.group(3):
            secs = (int(match.group(1)) * 60) + (int(match.group(3)))
        else:
            secs = int(match.group(1))

        await player.seek(secs * 1000)
        await ctx.send("Seeked.")


async def setup(bot):
    await bot.add_cog(Music(bot))
