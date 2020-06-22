from discord.ext import commands
from blackjack import *
from discord.ext.commands import has_permissions, CheckFailure
from collections import OrderedDict
from discord.ext.commands import errors
import datetime
import discord, aiohttp, asyncio, json, copy, os.path, copy, traceback, sys, aiosqlite

DEFAULT_AMOUNT = 10000

class BlackjackBot(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.games = {}
        self.activeGames = {}
        self.bankDB = None

    @commands.Cog.listener()
    async def on_ready(self):
        self.bot.loop.create_task(self.backgroundHourly())
        print('Logged in as {0}'.format(self.bot.user))
        await self.bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="Back To The Future 4"))

    @commands.Cog.listener()
    async def on_connect(self):
        await self.loadBank()
        self.loadChannels()
        #print('on_connect event fired!')

    @commands.Cog.listener()
    async def on_disconnect(self):
        self.saveChannels()
        #print('on_disconnect event fired!')

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        
        if payload.emoji.name == '✅':
            #if self.games[guildId]['task'] is not None:
            pass

        for key in list(self.activeGames):
            msg = self.activeGames[key]
            if payload.message_id != msg.id:
                continue

            guildId = str(payload.guild_id)
            game = self.getGame(guildId)

            if payload.emoji.name == '🆕':
                user = self.bot.get_user(payload.user_id)

                player = None
                playerIndex = -1
                for i in range(len(game.players)):
                    if game.players[i].user.id == payload.user_id:
                        player = game.players[i]
                        playerIndex = i
                        break
                if player is None:
                    return

                if player.bust or player.stood:
                    return

                if game.dealCards(player):
                    if player.getHand().minValue() > 21:
                        self.getGame(guildId).players[playerIndex].bust = True
                    await self.displayCards(guildId, msg)

                if not player.bust:
                    await msg.remove_reaction('🆕', user)

                await self.checkIfFinished(guildId)
            elif payload.emoji.name == '🆗':

                for i in range(len(game.players)):
                    if game.players[i].user.id == payload.user_id:
                        game.players[i].stood = True
                        break

                await self.checkIfFinished(guildId)
            elif payload.emoji.name == '⏬':
                for i in range(len(game.players)):
                    if game.players[i].user.id == payload.user_id and len(game.players[i].cards) == 2 and not game.players[i].doubleDown and not game.players[i].stood and not game.players[i].bust:
                        bal = await self.getBalance(guildId, str(payload.user_id))
                        if not bal - game.players[i].betAmount * 2 < 0:
                            if game.dealCards(game.players[i]):
                                self.getGame(guildId).players[i].doubleDown = True
                                self.getGame(guildId).players[i].betAmount = self.getGame(guildId).players[i].betAmount * 2
                                if game.players[i].getHand().minValue() > 21:
                                    self.getGame(guildId).players[i].bust = True
                                else:
                                    self.getGame(guildId).players[i].stood = True
                                await self.displayCards(guildId, msg)
                                await self.checkIfFinished(guildId)
                        break

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        skip = [
            errors.BadArgument,
            ValueError,
            errors.CommandNotFound,
            errors.BadArgument,
            errors.CheckFailure
        ]

        for c in skip:
            if isinstance(error, c):
                return

        traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)

    async def backgroundHourly(self):
        while True:
            await self.bot.wait_until_ready()

            # Reloads bank to avoid losing connection to DB
            await self.saveBank()
            await self.loadBank()

            await asyncio.sleep(3600)

    async def saveBank(self):
        try:
            await self.bankDB.commit()
        except ValueError:
            pass
        await self.bankDB.close()

    async def commitBank(self):
        b = await self.getBankDB()
        await b.commit()

    async def getBankDB(self) -> aiosqlite.Connection:
        if self.bankDB is None:
            self.bankDB = await aiosqlite.connect('bank.db')
        elif self.bankDB._connection is None:
            self.bankDB = await aiosqlite.connect('bank.db')

        return self.bankDB

    async def getBankCursor(self) -> aiosqlite.Cursor:
        if self.bankDB is None:
            self.bankDB = await aiosqlite.connect('bank.db')
        elif self.bankDB._connection is None:
            self.bankDB = await aiosqlite.connect('bank.db')

        cursor = None
        try:
            cursor = await self.bankDB.cursor()
        except ValueError:
            bankDB = await self.getBankDB()
            cursor = await bankDB.cursor()
        
        return cursor

    async def loadBank(self):
        self.bankDB = await aiosqlite.connect('bank.db')
        cursor = await self.bankDB.execute("""
            CREATE TABLE IF NOT EXISTS 'bank' (
                'guildID' TEXT NOT NULL,
                'userID' TEXT NOT NULL,
                'mcoins' INTEGER NOT NULL,
                'lastClaimedHourly' TEXT,
                PRIMARY KEY('guildID','userID')
            )
        """)

        await cursor.close()

    async def checkIfFinished(self, guildID: str):
        """ Check if the blackjack game is finished
        Loops through all players to check if they've busted or stood
        and ends the game if everyone is stood/busted.
        """
        if guildID not in self.activeGames:
            return
        game = self.getGame(guildID)
        finishedPlayers = 0
        for i in range(len(game.players)):
            if game.players[i].stood or game.players[i].bust:
                finishedPlayers += 1

        if finishedPlayers == len(self.getGame(guildID).players):
            if guildID in self.activeGames:
                del self.activeGames[guildID]
            await self.endGame(guildID)

    def isChannelSet(self, gid):
        if str(gid) not in self.games:
            return False
        elif self.games[str(gid)]['channel'] is None:
            return False
        return True

    def getGame(self, guild) -> Blackjack:
        return self.games[str(guild)]['game']

    def getChannel(self, guild) -> discord.TextChannel:
        return self.bot.get_channel(self.games[str(guild)]['channel'])

    def loadChannels(self):
        data = None
        if not os.path.exists('channels.json'):
            return

        with open('channels.json') as f:
            data = json.load(f)
        self.games = copy.deepcopy(data)
        for i in self.games:
            self.games[i]['game'] = None
            self.games[i]['task'] = None

    def saveChannels(self):
        data = {}
        for k in self.games:
            if self.games[k]['channel'] is not None:
                data[k] = {
                    'channel': self.games[k]['channel']
                }
        with open('channels.json', 'w') as f:
            json.dump(data, f)

    async def getBalance(self, guildID: str, userID) -> int:
        """ Returns the MCoins of a user
        
        Has to check if the server has a bank associated with it first,
        also if the user has an entry in the bank.
        """
        c = await self.getBankCursor()

        cursor = await c.execute('SELECT mcoins FROM bank WHERE guildID = ? and userID = ?', (guildID, str(userID)))
        result = await cursor.fetchone()

        coins = -1

        if result is None:
            cursor = await c.execute('INSERT INTO bank VALUES (?, ?, ?, NULL)', (guildID, str(userID), DEFAULT_AMOUNT))
            await self.commitBank()
            coins = DEFAULT_AMOUNT
        else:
            coins = result[0]

        return coins

    async def updateBalance(self, guildID: str, userID, coins: int, overwrite = False, lost = False) -> bool:
        """ Returns whether it successfully changes the users balance.

        Has an optional 'overwrite' argument to replace the users balance with a new value.
        The function has to check whether the channel is set, to see if there's an active game.

        The coins parameter can be negative or positive, using this function assumes that if you
        input a negative number, you want to subtract that amount of coins from the user.

        Lost parameter needed due to the way I implemented, giving users 1 MCoin if they won with 0 balance.
        """

        if not self.isChannelSet(guildID):
            return False

        bal = await self.getBalance(guildID, userID)

        c = await self.getBankCursor()

        if overwrite:
            cursor = await c.execute("UPDATE bank SET mcoins=? WHERE guildID = ? and userID = ?", (coins, guildID, str(userID)))
            await self.commitBank()
            return True

        if bal + coins < 0:
            return False

        if bal == 0 and self.getGame(guildID) is not None and coins == 0 and not lost:
            for p in self.getGame(guildID).players:
                if p.user.id == userID:
                    cursor = await c.execute("UPDATE bank SET mcoins=? WHERE guildID = ? and userID = ?", (1, guildID, str(userID)))
                    await self.commitBank()
                    return True
        
        cursor = await c.execute("UPDATE bank SET mcoins=? WHERE guildID = ? and userID = ?", (coins + bal, guildID, str(userID)))
        await self.commitBank()
        return True

    @commands.command(name='quit', no_pm=True)
    @commands.is_owner()
    async def quit(self, ctx):
        try:
            self.saveChannels()
        except Exception as e:
            print(type(e))
            print(e)
        try:
            await self.saveBank()
        except Exception as e:
            print(type(e))
            print(e)
        await self.bot.logout()

    @commands.command(name='set', no_pm=True)
    @has_permissions(administrator=True)
    async def setchannel(self, ctx, chan: discord.TextChannel):
        self.games[str(chan.guild.id)] = {
            "channel": chan.id,
            "game": None,
            "task": None
        }

        self.saveChannels()

        await ctx.message.channel.send('Gambling channel has been set to {0.mention}!'.format(chan))

    @setchannel.error
    async def setchannel_error(self, error, ctx):
        if isinstance(error, CheckFailure):
            await ctx.message.channel.send("Insufficient permissions!")

    @commands.command(name='hourly', no_pm=True)
    async def hourly(self, ctx):
        guildID = str(ctx.message.channel.guild.id)
        userID = str(ctx.message.author.id)

        if not self.isChannelSet(guildID):
            await ctx.message.channel.send('No channel is set for gambling! Use \'.set\'!')
            return

        await self.getBalance(guildID, userID)

        c = await self.getBankCursor()

        cursor = await c.execute('SELECT lastClaimedHourly FROM bank WHERE guildID=? AND userID=?', (guildID, userID))
        lastClaimed = await cursor.fetchone()

        HOURLY_BONUS = 100

        dtFormat = r"%Y/%m/%d - %H:%M:%S"

        timeNow = datetime.datetime.now()
        data = (timeNow.strftime(dtFormat), guildID, userID)

        isNullLast = False

        try:
            if lastClaimed is None:
                isNullLast = True
            elif lastClaimed[0] is None:
                isNullLast = True
        except TypeError:
            isNullLast = True

        if isNullLast:
            await self.updateBalance(guildID, userID, HOURLY_BONUS)
            await c.execute("UPDATE bank SET lastClaimedHourly=? WHERE guildID = ? and userID = ?", data)
            await self.commitBank()
            await ctx.message.channel.send('You claimed the hourly bonus of {0} MCoins!'.format(str(HOURLY_BONUS)))
        else:
            dateTimeClaimed = datetime.datetime.strptime(lastClaimed[0], dtFormat)

            diff = dateTimeClaimed - timeNow
            days, seconds = diff.days, diff.seconds
            hours = days * 24 + seconds // 3600
            minutes = (seconds % 3600) // 60
            seconds = seconds % 60

            if (timeNow - dateTimeClaimed).seconds > 3600:
                await self.updateBalance(guildID, userID, HOURLY_BONUS)
                await c.execute("UPDATE bank SET lastClaimedHourly=? WHERE guildID = ? and userID = ?", data)
                await self.commitBank()
                await ctx.message.channel.send('You claimed the hourly bonus of {0} MCoins!'.format(str(HOURLY_BONUS)))
            else:
                await ctx.message.channel.send('You can claim the hourly bonus in {0}m {1}s!'.format(str(minutes), str(seconds)))

    @commands.command(name='tip', no_pm=True)
    async def tip(self, ctx, userToTip: discord.User = None, amount: int = None):
        guildID = str(ctx.message.channel.guild.id)
        if not self.isChannelSet(guildID):
            await ctx.message.channel.send('No channel is set for gambling! Use \'.set\'!')
            return
        
        if userToTip is None:
            await ctx.message.channel.send('You did not specify who you wanted to tip!')
            return

        if amount is None:
            await ctx.message.channel.send('You did not specify how much you wanted to tip!')
            return
        
        if amount < 0:
            await ctx.message.channel.send('You cannot tip someone negative MCoins!')
            return

        tipper = ctx.message.author

        balance = await self.getBalance(guildID, tipper.id)

        bettingAmount = 0
        if self.getGame(guildID) is not None:
            for p in self.getGame(guildID).players:
                if p.user.id == tipper.id:
                    bettingAmount = p.betAmount
                    break

        if balance - bettingAmount - amount < 0:
            await ctx.message.channel.send('You do not have enough money to tip! (or you are currently betting)')
            return

        await self.updateBalance(guildID, tipper.id, -amount)
        await self.updateBalance(guildID, userToTip.id, amount)
        await ctx.message.channel.send('{0.mention} tipped {1.mention} {2} MCoins!'.format(tipper, userToTip, amount))

    @commands.command(name='leaderboards', aliases=['baltop', 'leaderboard', 'lb', 'scoreboard', 'sb'], no_pm=True)
    async def baltop(self, ctx):
        guildID = str(ctx.message.author.guild.id)

        c = await self.getBankCursor()

        cursor = await c.execute("SELECT userID, mcoins FROM bank WHERE guildID=? ORDER BY mcoins DESC;", (guildID,))
        listOfUsers = await cursor.fetchall()

        message = """```markdown
# Leaderboard"""
        n = 10 if len(listOfUsers) > 10 else len(listOfUsers)

        lastIndex = 0
        lastMoney = -1
        for _ in range(n):
            bankAccount = listOfUsers.pop(0)
            user = self.bot.get_user(int(bankAccount[0], base=10))
            if user is None:
                continue
            if lastMoney != bankAccount[1]:
                lastIndex += 1

            lastMoney = bankAccount[1]

            message +="""
[{0}]: {1} ({2})""".format(str(lastIndex), user.name, str(bankAccount[1]))


        message += """
```"""
        await ctx.message.channel.send(message)

    @commands.command(name='setmoney', no_pm=True)
    @commands.is_owner()
    async def setmoney(self, ctx, user: discord.User, amount: int):
        guildID = str(ctx.message.channel.guild.id)
        await self.updateBalance(guildID, str(user.id), amount, overwrite=True)
        await ctx.message.channel.send('Changed users balance!')

    @commands.command(name='balance', aliases=['bal', 'money', 'mcoin', 'coins', 'mcoins'], no_pm=True)
    async def bal(self, ctx, user: discord.User = None):
        u = ctx.message.author if user is None else user
        userID = ctx.author.id if user is None else user.id
        guildID = str(ctx.message.channel.guild.id)
        self.checkIfGuildHasBank(guildID)

        balance = await self.getBalance(guildID, userID)

        embed = discord.Embed(color=u.color)
        embed.set_thumbnail(url=str(u.avatar_url))
        embed.set_author(name="{0.name}#{0.discriminator}".format(u), icon_url=str(u.avatar_url))
        embed.add_field(name="MCoins", value=str(balance), inline=True)

        await ctx.message.channel.send(embed=embed)

    def checkIfGuildHasBank(self, guildID: str):
        #if guildID not in bank:
        #    bank[guildID] = {}
        pass

    @commands.command(name='maxbet', aliases=['betmax', 'ballout', 'mb'], no_pm=True)
    async def maxbet(self, ctx):
        maxAmt = await self.getBalance(str(ctx.message.channel.guild.id), ctx.author.id)
        await self.bet(ctx, maxAmt)

    def getPlayersInGame(self, guildID: str):
        players = ''
        gamePlayers = self.getGame(guildID).players
        for p in gamePlayers:
            players += p.user.mention + ', '
        players = players[:len(players)-2] + ' {0} entered.'.format('has' if len(gamePlayers) == 1 else 'have')

        return players

    @commands.command(name='bet', no_pm=True)
    async def bet(self, ctx, amount: int = None):
        if amount is None:
            await ctx.message.channel.send('You did not specify how much you want to bet!')
            return

        countdown = 30
        if not isinstance(countdown, int):
            return
        guildID = str(ctx.message.channel.guild.id)
        if not self.isChannelSet(guildID):
            await ctx.message.channel.send('No channel is set for gambling! Use \'.set\'!')
            return

        if ctx.message.channel.id != self.getChannel(guildID).id:
            return

        bal = await self.getBalance(guildID, ctx.author.id)

        if bal - amount < 0 or amount < 0:
            await self.getChannel(guildID).send('You do not have enough coins to bet! ({0})'.format(bal))
            return

        # if it is None, then there is no active game
        if self.getGame(guildID) is None:
            # We create a new Blackjack instance to start a game
            self.games[guildID]['game'] = Blackjack()
            self.getGame(guildID).addPlayer(Player(ctx.author, amount))
            msg = await self.getChannel(guildID).send('Game will begin in 30 seconds!\n' + self.getPlayersInGame(guildID))

            # Create a new task that counts down to begin the game
            self.games[guildID]['task'] = self.bot.loop.create_task(self.gameStartTimer(msg, countdown))
        elif self.getGame(guildID).getState() == STATE.PREP:
            for p in self.getGame(guildID).players:
                if p.user.id == ctx.author.id:
                    await self.getChannel(guildID).send('Either the game is full or you are already entered!')
                    return
            if not self.getGame(guildID).addPlayer(Player(ctx.author, amount)):
                await self.getChannel(guildID).send('Either the game is full or you are already entered!')
        else:
            await self.getChannel(guildID).send('The game has already started!')

    async def gameStartTimer(self, message: discord.Message, countdown=30):
        guildID = str(message.guild.id)

        t = countdown
        while t > 0:
            if len(self.getGame(guildID).players) == Blackjack.MAX_PLAYERS:
                break

            if t != 30:
                await message.edit(content='Game will begin in {0} seconds!\n{1}'.format(t, self.getPlayersInGame(guildID)))
            t -= 5
            await asyncio.sleep(5)
        self.getGame(guildID).start()
        players = 'Players are: '
        for i in self.getGame(message.guild.id).players:
            players += i.user.mention + '({0}), '.format(i.betAmount)
        players = players[:len(players)-2] + '.'

        await message.edit(content='Game has begun!\n' + players)

        self.games[guildID]['task'] = self.bot.loop.create_task(self.gamePlayerChoices(guildID))

    async def gamePlayerChoices(self, guildID: str):
        self.activeGames[guildID] = await self.displayCards(guildID)

        await self.activeGames[guildID].add_reaction('🆗')
        await self.activeGames[guildID].add_reaction('🆕')
        #await self.activeGames[guildID].add_reaction('✅')
        await self.activeGames[guildID].add_reaction('⏬')

        t = 60
        message = await self.getChannel(guildID).send('Game will end in {0} seconds!'.format(t))

        while t > 0:
            if guildID not in self.activeGames:
                await message.edit(content='Game has ended!')
                break

            if t != 60 and t % 5 == 0:
                await message.edit(content='Game will end in {0} seconds!'.format(t))
            t -= 1
            await asyncio.sleep(1)

        if guildID in self.activeGames:
            del self.activeGames[guildID]
            await self.endGame(guildID)

    async def endGame(self, guildID: str):
        dealerHand = self.getGame(guildID).getDealer().getHand()
        dealerValue = dealerHand.minValue() if dealerHand.maxValue() > 21 else dealerHand.maxValue()

        while dealerValue < 17:
            self.getGame(guildID).dealCards(self.getGame(guildID).getDealer())
            dealerValue = self.getGame(guildID).getDealer().getHand().minValue()

            if self.getGame(guildID).getDealer().getHand().maxValue() == 21:
                break

        dealerHand = self.getGame(guildID).getDealer().getHand()
        dealerValue = dealerHand.minValue()

        if dealerHand.maxValue() <= 21:
            dealerValue = dealerHand.maxValue()

        if dealerValue == 21:
            tiedPlayers = ''
            lostPlayers = ''
            loses = 0
            ties = 0
            for p in self.getGame(guildID).players:
                if p.has21():
                    tiedPlayers += p.user.mention + ', '
                    ties += 1
                    continue
                await self.updateBalance(guildID, p.user.id, -p.betAmount, lost=True)
                lostPlayers += p.user.mention + ', '
                loses += 1
            tiedPlayers = tiedPlayers[:len(tiedPlayers)-2]
            lostPlayers = lostPlayers[:len(lostPlayers)-2]
            endMessage = 'Dealer has blackjack!'
            if ties > 0:
                endMessage += '\n' + tiedPlayers + ' also got blackjack.'
            if loses > 0:
                endMessage += '\n{0} {1} lost.'.format(lostPlayers, ('has' if loses == 1 else 'have'))
            await self.getChannel(guildID).send(endMessage)
        elif dealerValue > 21:
            lostPlayers = ''
            loses = 0
            for p in self.getGame(guildID).players:
                if not p.bust:
                    if p.has21() and len(p.cards) == 2:
                        await self.updateBalance(guildID, p.user.id, int(p.betAmount * 1.5))
                    else:
                        await self.updateBalance(guildID, p.user.id, p.betAmount)
                    continue
                lostPlayers += p.user.mention + ', '
                await self.updateBalance(guildID, p.user.id, -p.betAmount, lost=True)
                loses += 1
            lostPlayers = lostPlayers[:len(lostPlayers)-2]

            endMessage = 'Dealer has busted!'
            if loses > 0:
                endMessage += '\n{0} {1} lost!'.format(lostPlayers, ('has' if loses == 1 else 'have'))
                if len(self.getGame(guildID).players) != loses:
                    endMessage += '\nAll other players win!'
            else:
                endMessage += '\nAll players win!'
            await self.getChannel(guildID).send(endMessage)
        else:
            winPlayers = ''
            tiedPlayers = ''
            lostPlayers = ''
            won = 0
            tied = 0
            lost = 0
            for p in self.getGame(guildID).players:
                pHand = p.getHand()
                if p.bust:
                    lostPlayers += p.user.mention + ', '
                    await self.updateBalance(guildID, p.user.id, -p.betAmount, lost=True)
                    lost += 1
                elif p.has21():
                    winPlayers += p.user.mention + ', '
                    await self.updateBalance(guildID, p.user.id, int(p.betAmount * (1.5 if len(p.cards) == 2 else 1)))
                    won += 1
                elif pHand.minValue() > dealerValue or (pHand.maxValue() > dealerValue and pHand.maxValue() <= 21):
                    winPlayers += p.user.mention + ', '
                    await self.updateBalance(guildID, p.user.id, p.betAmount)
                    won += 1
                elif (pHand.minValue() == dealerValue and pHand.maxValue() ) or pHand.maxValue() == dealerValue:
                    tiedPlayers += p.user.mention + ', '
                    tied += 1
                elif (pHand.maxValue() <= 21) and (pHand.minValue() > dealerValue or pHand.maxValue() > dealerValue):
                    winPlayers += p.user.mention + ', '
                    await self.updateBalance(guildID, p.user.id, p.betAmount)
                    won += 1
                elif dealerValue > pHand.minValue() and (dealerValue > pHand.maxValue() and pHand.maxValue() <= 21):
                    lostPlayers += p.user.mention + ', '
                    await self.updateBalance(guildID, p.user.id, -p.betAmount, lost=True)
                    lost += 1
                elif dealerValue > pHand.maxValue() and pHand.maxValue() <= 21:
                    lostPlayers += p.user.mention + ', '
                    await self.updateBalance(guildID, p.user.id, -p.betAmount, lost=True)
                    lost += 1
                elif dealerValue > pHand.minValue() and pHand.maxValue() > 21:
                    lostPlayers += p.user.mention + ', '
                    await self.updateBalance(guildID, p.user.id, -p.betAmount, lost=True)
                    lost += 1

            tiedPlayers = tiedPlayers[:len(tiedPlayers)-2]
            lostPlayers = lostPlayers[:len(lostPlayers)-2]
            winPlayers = winPlayers[:len(winPlayers)-2]
            endMessage = ''

            if won > 0:
                endMessage += '{0} {1} won!'.format(winPlayers, ('has' if won == 1 else 'have'))
            if tied > 0:
                if endMessage != '':
                    endMessage += '\n'
                endMessage += '{0} pushed!'.format(tiedPlayers)
            if lost > 0:
                if endMessage != '':
                    endMessage += '\n'
                endMessage += '{0} {1} lost!'.format(lostPlayers, ('has' if lost == 1 else 'have'))

            if endMessage == '':
                endMessage = 'This should not happen.'

            await self.getChannel(guildID).send(endMessage)

        await self.displayCards(guildID, None, False)
        self.games[guildID]['game'] = None
        self.games[guildID]['task'] = None

    async def displayCards(self, guildID: str, editMsg=None, dealerHide=True, startMsg='') -> discord.Message:
        game = self.getGame(guildID)
        cards = ''

        if dealerHide:
            cards += 'Hidden|' + game.getDealer().cards[1].toString()
        else:
            for i in game.getDealer().cards:
                cards += i.toString() + '|'
            cards = cards[:len(cards)-1]
            if game.getDealer().getHand().minValue() > 21:
                cards += ' (BUST)'

        handVal = game.getDealer().getHand().minValue()
        if handVal != game.getDealer().getHand().maxValue():
            dealer = game.getDealer()
            h = dealer.getHand()
            handVal = str(handVal) + '/' + str(h.maxValue())

        message = startMsg + '\n'

        message += """```css
Dealer: {0} {1}
```
```yaml
""".format(cards, ('' if dealerHide else '(%s)' % handVal))

        for p in game.players:
            cards = ''
            for c in p.cards:
                cards += c.toString() + '|'
            cards = cards[:len(cards)-1]

            if p.bust:
                cards += " (BUST)"

            handVal = p.getHand().minValue()
            if handVal != p.getHand().maxValue():
                handVal = str(handVal) + '/' + str(p.getHand().maxValue())

            message += """
{0} ({1}): {2} {3}
""".format(p.user.display_name, p.betAmount, cards, ('' if p.bust else ('(%s)' % str(handVal)) ))

        message += """
```
"""

        if editMsg is None:
            return await self.getChannel(guildID).send(message)

        await editMsg.edit(content=message)
        return None

bot = commands.Bot(command_prefix=commands.when_mentioned_or('.'))
bot.add_cog(BlackjackBot(bot))

bot.run('')