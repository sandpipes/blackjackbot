from enum import Enum
import random
import collections

class STATE(Enum):
    PREP = 0
    DEALING = 1
    PLAYER_TURN = 2
    RESULTS = 3


class Card:
    def __init__(self, suit, rank):
        self.suit = suit
        self.rank = rank
        self.pic = ''

    def toString(self) -> str:
        if self.suit == 'Hearts':
            return '❤️' + self.rank
        elif self.suit == 'Spades':
            return '♠️' + self.rank
        elif self.suit == 'Clubs':
            return '♣️' + self.rank
        elif self.suit == 'Diamonds':
            return '♦️' + self.rank
        return 'INVALID CARD'

class Hand:
    def __init__(self, amount=0, aces=0):
        self.value = amount
        self.aces = aces

    def add(self, i):
        self.value += i

    def maxValue(self) -> int:
        if self.aces > 1:
            return self.value + self.aces - 1 + 11
        return self.value + self.aces * 11

    def minValue(self) -> int:
        return self.value + self.aces

class Deck:
    cardFaces = ['Hearts','Spades','Clubs','Diamonds']
    cardNumbers = ['Ace','2','3','4','5','6','7','8','9','10','Q','K','J']

    def __init__(self):
        self.cards = []

    def refresh(self):
        self.cards = []
        for _ in range(7):
            for f in Deck.cardFaces:
                for n in Deck.cardNumbers:
                    self.cards.append(Card(f, n))
    
    def shuffle(self):
        random.shuffle(self.cards)

    def getCard(self) -> Card:
        return self.cards.pop()

class Player:
    def __init__(self, user, betAmount: int):
        self.user = user
        self.betAmount = betAmount
        self.cards = []
        self.stood = False
        self.bust = False
        self.doubleDown = False

    def bet(self, amount):
        self.betAmount += amount

    def getHand(self) -> Hand:
        hand = Hand()
        for i in self.cards:
            if i.rank == 'Ace':
                hand.aces += 1
            elif i.rank == 'Q' or i.rank =='K' or i.rank == 'J':
                hand.add(10)
            else:
                hand.add(int(i.rank))
        return hand

    def has21(self) -> bool:
        hand = self.getHand()
        if hand.aces == 1 and hand.value == 10:
            return True
        elif hand.value == 21 and hand.aces == 0:
            return True
        elif hand.value + hand.aces == 21:
            return True
        return False

class Dealer(Player):
    pass

class Blackjack:
    MAX_PLAYERS = 10
    def __init__(self):
        self.dealer = Dealer(None, -1)
        self.players = []
        self.deck = Deck()
        self.state = STATE.PREP

    def addPlayer(self, p: Player) -> bool:
        if p in self.players or self.state != STATE.PREP or len(self.players) == Blackjack.MAX_PLAYERS:
            return False
        self.players.append(p)
        return True

    def getState(self) -> STATE:
        return self.state

    def start(self):
        self.state = STATE.DEALING
        self.deck.refresh()
        self.deck.shuffle()
        self.dealCards(self.dealer, 2)
        for i in self.players:
            self.dealCards(i, 2)

    def dealCards(self, player: Player, amount=1) -> bool:
        if player.stood or player.bust:
            return False
        for _ in range(amount):
            player.cards.append(self.deck.getCard())
        return True

    def getDealer(self) -> Dealer:
        return self.dealer