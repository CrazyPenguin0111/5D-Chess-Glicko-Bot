import glicko2
import discord
from discord.ext import commands, tasks
from glicko2 import Player
import sqlite3
import logging
from datetime import datetime, timedelta


int_rating = 1400
int_rd = 350
int_vol = 0.06
rd_cutoff = 250

# Setup logging
logging.basicConfig(level=logging.INFO)

intents = discord.Intents.all()
bot = commands.Bot(command_prefix='$', intents=intents)

# Connect to SQLite database
conn = sqlite3.connect('players.db')
c = conn.cursor()

# Create players table if it doesn't exist
c.execute('''CREATE TABLE IF NOT EXISTS players
             (discord_id INTEGER PRIMARY KEY, rating REAL, rd REAL, vol REAL, last_match TEXT, matches_played INTEGER, wins INTEGER, losses INTEGER, draws INTEGER)''')
conn.commit()

# Create pending_matches table if it doesn't exist
c.execute('''CREATE TABLE IF NOT EXISTS pending_matches
             (reporter_id INTEGER, opponent_id INTEGER, result TEXT, timestamp TEXT)''')
conn.commit()

# Set the ID of the channel where the bot should respond
ALLOWED_CHANNEL_ID = 1257478537263317073  # Replace with your channel ID

def is_allowed_channel(ctx):
    return ctx.channel.id == ALLOWED_CHANNEL_ID

def player_exists(discord_id):
    c.execute('SELECT * FROM players WHERE discord_id = ?', (discord_id,))
    return c.fetchone() is not None

def create_player(discord_id):
    try:
        if player_exists(discord_id):
            return False
        # Initialize the player with rating 1400, rd 350, and vol 0.06
        player = Player(rating=int_rating, rd=int_rd, vol=int_vol)
        now = datetime.utcnow().isoformat()
        c.execute(
            'INSERT INTO players (discord_id, rating, rd, vol, last_match, matches_played, wins, losses, draws) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (discord_id, player.rating, player.rd, player.vol, now, 0, 0, 0, 0))
        conn.commit()
        return True
    except Exception as e:
        logging.error(f"Error creating player: {e}")
        return False

def get_player(discord_id):
    try:
        c.execute('SELECT * FROM players WHERE discord_id = ?', (discord_id,))
        player_data = c.fetchone()
        if player_data:
            return Player(player_data[1], player_data[2], player_data[3])
        return None
    except Exception as e:
        logging.error(f"Error getting player: {e}")
        return None

def get_fetch_user(bot, user_id):
    user = bot.get_user(user_id)
    if not user:  # the user isn't in the cache, or it doesn't exist
        try:
            user = bot.fetch_user(user_id)
        except discord.NotFound:  # fetch_user raises an error if the user doesn't exist
            user = None
    return user

def update_glicko(discord_id, rating, rd, vol):
    try:
        c.execute('UPDATE players SET rating = ?, rd = ?, vol = ? WHERE discord_id = ?',
                  (rating, rd, vol, discord_id))
        conn.commit()
    except Exception as e:
        logging.error(f"Error updating Glicko scores: {e}")

def update_player_stats(discord_id, player, win=False, loss=False, draw=False):
    try:
        now = datetime.utcnow().isoformat()
        c.execute(
            'UPDATE players SET rating = ?, rd = ?, vol = ?, last_match = ?, matches_played = matches_played + 1 WHERE discord_id = ?',
            (player.rating, player.rd, player.vol, now, discord_id))
        if win:
            c.execute('UPDATE players SET wins = wins + 1 WHERE discord_id = ?', (discord_id,))
        if loss:
            c.execute('UPDATE players SET losses = losses + 1 WHERE discord_id = ?', (discord_id,))
        if draw:
            c.execute('UPDATE players SET draws = draws + 1 WHERE discord_id = ?', (discord_id,))
        conn.commit()
    except Exception as e:
        logging.error(f"Error updating player: {e}")

def report_pending_match(reporter_id, opponent_id, result):
    try:
        now = datetime.utcnow().isoformat()
        c.execute('INSERT INTO pending_matches (reporter_id, opponent_id, result, timestamp) VALUES (?, ?, ?, ?)',
                  (reporter_id, opponent_id, result, now))
        conn.commit()
    except Exception as e:
        logging.error(f"Error reporting pending match: {e}")

def get_pending_match(reporter_id, opponent_id, timestamp):
    try:
        c.execute(
            'SELECT * FROM pending_matches WHERE reporter_id = ? AND opponent_id = ? AND timestamp = ?',
            (reporter_id, opponent_id, timestamp))
        return c.fetchone()
    except Exception as e:
        logging.error(f"Error getting pending match: {e}")
        return None

def get_pending_matches(reporter_id, opponent_id):
    try:
        c.execute(
            'SELECT * FROM pending_matches WHERE (reporter_id = ? AND opponent_id = ?) OR (reporter_id = ? AND opponent_id = ?)',
            (reporter_id, opponent_id, opponent_id, reporter_id))
        return c.fetchall()
    except Exception as e:
        logging.error(f"Error getting pending matches: {e}")
        return []

def delete_pending_match(reporter_id, opponent_id, timestamp):
    try:
        c.execute(
            'DELETE FROM pending_matches WHERE reporter_id = ? AND opponent_id = ? AND timestamp = ?',
            (reporter_id, opponent_id, timestamp))
        conn.commit()
    except Exception as e:
        logging.error(f"Error deleting pending match: {e}")

def finalize_match(reporter_id, opponent_id, result, timestamp):
    try:
        reporter_player = get_player(reporter_id)
        opponent_player = get_player(opponent_id)

        if result == 'd':

            r_rd = reporter_player.rd
            r_rating = reporter_player.rating

            o_rd = opponent_player.rd
            o_rating = opponent_player.rating

            reporter_player.update_player([o_rating], [o_rd], [0.5])
            opponent_player.update_player([r_rating], [r_rd], [0.5])

            update_player_stats(reporter_id, reporter_player, draw=True)
            update_player_stats(opponent_id, opponent_player, draw=True)

            update_glicko(reporter_id, reporter_player.rating, reporter_player.rd, reporter_player.vol)
            update_glicko(opponent_id, opponent_player.rating, opponent_player.rd, opponent_player.vol)

        else:
            if result == 'w':
                winner_id = reporter_id
                loser_id = opponent_id
            else:
                winner_id = opponent_id
                loser_id = reporter_id

            winner_player = get_player(winner_id)
            loser_player = get_player(loser_id)

            w_rating = winner_player.rating
            w_rd = winner_player.rating

            l_rating = loser_player.rating
            l_rd = loser_player.rd

            winner_player.update_player([l_rating], [l_rd], [1])
            loser_player.update_player([w_rating], [w_rd],[0])

            update_player_stats(winner_id, winner_player, win=True)
            update_player_stats(loser_id, loser_player, loss=True)

            update_glicko(winner_id, winner_player.rating, winner_player.rd, winner_player.vol)
            update_glicko(loser_id, loser_player.rating, loser_player.rd, loser_player.vol)

    except Exception as e:
        logging.error(f"Error finalizing match: {e}")

@tasks.loop(minutes=5)
async def cleanup_pending_matches():
    try:
        expiration_time = datetime.utcnow() - timedelta(minutes=20)
        c.execute('DELETE FROM pending_matches WHERE timestamp <= ?', (expiration_time.isoformat(),))
        conn.commit()
        logging.info('Cleanup: Removed pending matches older than 20 minutes.')
    except Exception as e:
        logging.error(f"Error during cleanup of pending matches: {e}")

@bot.event
async def on_ready():
    logging.info(f'Logged in as {bot.user}')
    cleanup_pending_matches.start()

@bot.command()
async def register(ctx):
    if not is_allowed_channel(ctx):
        return

    if player_exists(ctx.author.id):
        embed = discord.Embed(description=f'{ctx.author.mention}, you are already registered.')
        await ctx.send(embed=embed)
        return

    if create_player(ctx.author.id):
        embed = discord.Embed(description=f'{ctx.author.mention}, you have been registered with a starting rating of 1400.')
        await ctx.send(embed=embed)
    else:
        embed = discord.Embed(description=f'{ctx.author.mention}, there was an error registering you. Please try again.')
        await ctx.send(embed=embed)

@bot.command()
async def rep(ctx, result: str, opponent: discord.Member):
    if not is_allowed_channel(ctx):
        return

    if result not in ['w', 'l', 'd']:
        embed = discord.Embed(description="Invalid result. Use 'w' for win, 'l' for loss, or 'd' for draw.")
        await ctx.send(embed=embed)
        return

    if not player_exists(ctx.author.id):
        embed = discord.Embed(description=f'{ctx.author.mention}, you need to register first using $register.')
        await ctx.send(embed=embed)
        return

    if not player_exists(opponent.id):
        embed = discord.Embed(description=f'{opponent.mention} is not registered. They need to register first using $register.')
        await ctx.send(embed=embed)
        return

    if opponent.id == ctx.author.id:
        embed = discord.Embed(description=f"You cannot report a match against yourself")
        await ctx.send(embed=embed)
        return

    report_pending_match(ctx.author.id, opponent.id, result)
    pending_matches = get_pending_matches(ctx.author.id, opponent.id)

    for match in pending_matches:
        if result == 'w':
            if match[2] == 'l':
                finalize_match(ctx.author.id, opponent.id, result, match[3])
                delete_pending_match(ctx.author.id, opponent.id, match[3])
                author_data = c.execute('SELECT rating, wins, losses, draws, rd FROM players WHERE discord_id = ?',
                                        (ctx.author.id,)).fetchone()

                opponent_data = c.execute('SELECT rating, wins, losses, draws, rd FROM players WHERE discord_id = ?',
                                        (opponent.id,)).fetchone()

                if author_data and opponent_data:

                    if author_data[4] > rd_cutoff:
                        a_added_marker = '?'
                    else:
                        a_added_marker = ''

                    if opponent_data[4] > rd_cutoff:
                        o_added_marker = '?'
                    else:
                        o_added_marker = ''

                    response = (
                        f"{ctx.author.mention}:\n"
                        f"Rating: {round(author_data[0],1)}{a_added_marker}\n"
                        f"Wins: {author_data[1]} | "
                        f"Losses: {author_data[2]} | "
                        f"Draws: {author_data[3]}\n\n"
                        f"{opponent.mention}\n"
                        f"Rating: {round(opponent_data[0],1)}{o_added_marker}\n"
                        f"Wins: {opponent_data[1]} | "
                        f"Losses: {opponent_data[2]} | "
                        f"Draws: {opponent_data[3]}"
                    )
                    embed = discord.Embed(description=f'Match confirmed and reported: {ctx.author.mention} vs {opponent.mention}\n{response}')
                    await ctx.send(embed=embed)
                return
        elif result == 'l':
            if match[2] == 'w':
                finalize_match(ctx.author.id, opponent.id, result, match[3])
                delete_pending_match(ctx.author.id, opponent.id, match[3])

                author_data = c.execute('SELECT rating, wins, losses, draws, rd FROM players WHERE discord_id = ?',
                                        (ctx.author.id,)).fetchone()

                opponent_data = c.execute('SELECT rating, wins, losses, draws, rd FROM players WHERE discord_id = ?',
                                          (opponent.id,)).fetchone()

                if author_data and opponent_data:

                    if author_data[4] > rd_cutoff:
                        a_added_marker = '?'
                    else:
                        a_added_marker = ''

                    if opponent_data[4] > rd_cutoff:
                        o_added_marker = '?'
                    else:
                        o_added_marker = ''

                    response = (
                        f"{ctx.author.mention}:\n"
                        f"Rating: {round(author_data[0],1)}{a_added_marker}\n"
                        f"Wins: {author_data[1]} | "
                        f"Losses: {author_data[2]} | "
                        f"Draws: {author_data[3]}\n\n"
                        f"{opponent.mention}\n"
                        f"Rating: {round(opponent_data[0],1)}{o_added_marker}\n"
                        f"Wins: {opponent_data[1]} | "
                        f"Losses: {opponent_data[2]} | "
                        f"Draws: {opponent_data[3]}"
                    )
                    embed = discord.Embed(description=f'Match confirmed and reported: {ctx.author.mention} vs {opponent.mention}\n{response}')
                    await ctx.send(embed=embed)
                return
        elif result == 'd':
            if match[2] == 'd' and match[0] != ctx.author.id:
                finalize_match(ctx.author.id, opponent.id, result, match[3])
                delete_pending_match(ctx.author.id, opponent.id, match[3])

                author_data = c.execute('SELECT rating, wins, losses, draws, rd FROM players WHERE discord_id = ?',
                                        (ctx.author.id,)).fetchone()

                opponent_data = c.execute('SELECT rating, wins, losses, draws, rd FROM players WHERE discord_id = ?',
                                          (opponent.id,)).fetchone()

                if author_data and opponent_data:

                    if author_data[4] > rd_cutoff:
                        a_added_marker = '?'
                    else:
                        a_added_marker = ''

                    if opponent_data[4] > rd_cutoff:
                        o_added_marker = '?'
                    else:
                        o_added_marker = ''

                    response = (
                        f"{ctx.author.mention}:\n"
                        f"Rating: {round(author_data[0],1)}{a_added_marker}\n"
                        f"Wins: {author_data[1]} | "
                        f"Losses: {author_data[2]} | "
                        f"Draws: {author_data[3]}\n\n"
                        f"{opponent.mention}\n"
                        f"Rating: {round(opponent_data[0],1)}{o_added_marker}\n"
                        f"Wins: {opponent_data[1]} | "
                        f"Losses: {opponent_data[2]} | "
                        f"Draws: {opponent_data[3]}"
                    )
                    embed = discord.Embed(description=f'Match confirmed and reported: {ctx.author.mention} vs {opponent.mention}\n{response}')
                    await ctx.send(embed=embed)
                return

    embed = discord.Embed(description=f'Match reported: {ctx.author.mention} vs {opponent.mention} Awaiting confirmation from {opponent.mention}.')
    await ctx.send(embed=embed)

@bot.command()
async def cancel(ctx, opponent: discord.Member):
    if not is_allowed_channel(ctx):
        return

    pending_matches = get_pending_matches(ctx.author.id, opponent.id)

    if not pending_matches:
        embed = discord.Embed(description=f'{ctx.author.mention}, there is no pending match report against {opponent.mention} to cancel.')
        await ctx.send(embed=embed)
        return

    for match in pending_matches:
        if match[0] == ctx.author.id:
            delete_pending_match(ctx.author.id, opponent.id, match[3])
            embed = discord.Embed(description=f'{ctx.author.mention}, your pending match report against {opponent.mention} has been canceled.')
            await ctx.send(embed=embed)
            return

    embed = discord.Embed(description=f'{ctx.author.mention}, there is no pending match report against {opponent.mention} to cancel.')
    await ctx.send(embed=embed)

@bot.command()
async def leaderboard(ctx, rk: int = None):
    if not is_allowed_channel(ctx):
        return

    try:
        three_months_ago = datetime.utcnow() - timedelta(days=90)
        c.execute('SELECT * FROM players WHERE last_match >= ? AND matches_played >= 4 ORDER BY rating DESC LIMIT 10',
                  (three_months_ago.isoformat(),))
        top_10 = c.fetchall()
        response = "Leaderboard (last 3 months):\n"
        for i, player_data in enumerate(top_10, start=1):

            player_user = get_fetch_user(bot, player_data[0])
            user_name = player_user.name

            response += f"{i}. {user_name} {round(player_data[1],1)}\n"

        player_data = c.execute('SELECT matches_played, rating, rd FROM players WHERE discord_id = ?',
                                   (ctx.author.id,)).fetchone()

        author_name = ctx.author.name

        if player_data[0] < 4:
            response += "\nYou must report at least 4 rated matches to be placed on the leaderboard."
        else:
            c.execute(
                'SELECT * FROM players WHERE last_match >= ? AND matches_played >= 4 ORDER BY rating DESC',
                (three_months_ago.isoformat(),))
            ranked_list = c.fetchall()
            for index, player_data in enumerate(ranked_list):
                rank = index + 1
                if rk is None:
                    if player_data and author_name not in response:
                        if player_data[0] == ctx.author.id:

                            if rank > 1:
                                player_data_up = ranked_list[rank - 1]
                                h_user = get_fetch_user(bot, player_data_up[0])
                                h_user_name = h_user.name
                                response += f"\n{rank - 1}. {h_user_name} {round(player_data_up[1], 1)}"

                            response += f"\n{rank}. {ctx.author.name} {round(player_data[1],1)}"

                            if rank < len(ranked_list):
                                player_data_down = ranked_list[rank + 1]
                                d_user = get_fetch_user(bot, player_data_down[0])
                                d_user_name = d_user.name
                                response += f"\n{rank + 1}. {d_user_name} {round(player_data_down[1], 1)}"
                else:
                    if rank == rk and rk > 9:

                        player_data_up = ranked_list[rank - 1]
                        h_user = get_fetch_user(bot, player_data_up[0])
                        h_user_name = h_user.name
                        response += f"\n{rank - 1}. {h_user_name} {round(player_data_up[1], 1)}"

                        user = get_fetch_user(bot, player_data[0])
                        user_name = user.name
                        response += f"\n{rank}. {user_name} {round(player_data[1], 1)}"

                        if rk < len(ranked_list):
                            player_data_down = ranked_list[rank + 1]
                            d_user = get_fetch_user(bot, player_data_down[0])
                            d_user_name = d_user.name
                            response += f"\n{rank + 1}. {d_user_name} {round(player_data_down[1], 1)}"


        embed = discord.Embed(description=response)
        await ctx.send(embed=embed)
    except Exception as e:
        logging.error(f"Error fetching leaderboard: {e}")
        embed = discord.Embed(description="An error occurred while fetching the leaderboard.")
        await ctx.send(embed=embed)

@bot.command()
async def stale_leaderboard(ctx, rk: int = None):
    if not is_allowed_channel(ctx):
        return

    try:
        c.execute('SELECT * FROM players WHERE matches_played >= 4 ORDER BY rating DESC LIMIT 10')
        top_10 = c.fetchall()
        response = "Leaderboard:\n"
        for i, player_data in enumerate(top_10, start=1):

            player_user = get_fetch_user(bot, player_data[0])
            user_name = player_user.name

            response += f"{i}. {user_name} {round(player_data[1],1)}\n"

        player_data = c.execute('SELECT matches_played, rating, rd FROM players WHERE discord_id = ?',
                                   (ctx.author.id,)).fetchone()

        author_name = ctx.author.name

        if player_data[0] < 4:
            response += "\nYou must report at least 4 rated matches to be placed on the leaderboard."
        else:
            c.execute(
                'SELECT * FROM players WHERE matches_played >= 4 ORDER BY rating DESC'
            )
            ranked_list = c.fetchall()
            for index, player_data in enumerate(ranked_list):
                rank = index + 1
                if rk is None:
                    if player_data and author_name not in response:
                        if player_data[0] == ctx.author.id:

                            if rank > 1:
                                player_data_up = ranked_list[rank - 1]
                                h_user = get_fetch_user(bot, player_data_up[0])
                                h_user_name = h_user.name
                                response += f"\n{rank - 1}. {h_user_name} {round(player_data_up[1], 1)}"

                            response += f"\n{rank}. {ctx.author.name} {round(player_data[1],1)}"

                            if rank < len(ranked_list):
                                player_data_down = ranked_list[rank + 1]
                                d_user = get_fetch_user(bot, player_data_down[0])
                                d_user_name = d_user.name
                                response += f"\n{rank + 1}. {d_user_name} {round(player_data_down[1], 1)}"
                else:
                    if rank == rk and rk > 9:

                        player_data_up = ranked_list[rank-1]
                        h_user = get_fetch_user(bot,player_data_up[0])
                        h_user_name = h_user.name
                        response += f"\n{rank-1}. {h_user_name} {round(player_data_up[1], 1)}"

                        user = get_fetch_user(bot,player_data[0])
                        user_name = user.name
                        response += f"\n{rank}. {user_name} {round(player_data[1], 1)}"

                        if rk < len(ranked_list):
                            player_data_down = ranked_list[rank+1]
                            d_user = get_fetch_user(bot,player_data_down[0])
                            d_user_name = d_user.name
                            response += f"\n{rank+1}. {d_user_name} {round(player_data_down[1], 1)}"

        embed = discord.Embed(description=response)
        await ctx.send(embed=embed)
    except Exception as e:
        logging.error(f"Error fetching leaderboard: {e}")
        embed = discord.Embed(description="An error occurred while fetching the leaderboard.")
        await ctx.send(embed=embed)

@bot.command()
async def stats(ctx):
    if not is_allowed_channel(ctx):
        return

    try:
        player_data = c.execute('SELECT rating, wins, losses, draws, rd FROM players WHERE discord_id = ?',
                                (ctx.author.id,)).fetchone()
        if player_data:
            if player_data[4] > rd_cutoff:
                added_marker = '?'
            else:
                added_marker = ''

            response = (
                f"{ctx.author.mention}, here are your stats:\n"
                f"Rating: {round(player_data[0],1)}{added_marker}\n"
                f"Wins: {player_data[1]} | "
                f"Losses: {player_data[2]} | "
                f"Draws: {player_data[3]}"
            )
        else:
            response = f"{ctx.author.mention}, you are not registered. Use $register to register."
        embed = discord.Embed(description=response)
        await ctx.send(embed=embed)
    except Exception as e:
        logging.error(f"Error fetching stats: {e}")
        embed = discord.Embed(description="An error occurred while fetching your stats.")
        await ctx.send(embed=embed)

@bot.command()
async def looking(ctx):
    if not is_allowed_channel(ctx):
        return

    try:
        role = discord.utils.get(ctx.guild.roles, name='Looking')
        if role in ctx.author.roles:
            await ctx.author.remove_roles(role)
            embed = discord.Embed(description=f'{ctx.author.mention} is no longer looking for a match.')
            await ctx.send(embed=embed)
        else:
            await ctx.author.add_roles(role)
            embed = discord.Embed(description=f'{ctx.author.mention} is now looking for a match.')
            await ctx.send(embed=embed)
    except Exception as e:
        logging.error(f"Error toggling looking role: {e}")
        embed = discord.Embed(description="An error occurred while toggling the looking role.")
        await ctx.send(embed=embed)

@bot.command()
async def help_bot(ctx):
    if not is_allowed_channel(ctx):
        return

    try:
        response = (
            "$register - Register yourself with a starting rating of 1400.\n"
            "$rep [w/l/d] @opponent - Report a match result.\n"
            "$cancel @opponent - Cancel your pending match report against the opponent.\n"
            "$leaderboard - Display the leaderboard.\n"
            "$stale_leaderboard - Display the leaderboard of people who don't play matches.\n"
            "$stats - Show your rating and the number of wins, losses, and draws.\n"
            "$looking - Toggle the looking role.\n"
            "$help_bot - Display this help message."
        )
        embed = discord.Embed(description=response)
        await ctx.send(embed=embed)
    except Exception as e:
        logging.error(f"Error displaying help: {e}")
        embed = discord.Embed(description="An error occurred while displaying the help message.")
        await ctx.send(embed=embed)

# Remember to replace 'YOUR_BOT_TOKEN' with your actual bot token
with open('bot_token.txt', 'r') as file:
    for line in file:
        if ' = ' in line:
            line_list = line.split(' = ')
            token = line_list[-1]
            token = token.replace('\n', '')
            bot.run(token)

# Close the database connection when the bot stops
@bot.event
async def on_disconnect():
    conn.close()
