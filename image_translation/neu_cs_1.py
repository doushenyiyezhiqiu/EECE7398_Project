def deal(number_of_players, number_of_cards, cards):
    can_dealt = min(number_of_players*number_of_cards, len(cards))
    if number_of_players == 0:
        return [[]]
    result = [[] for _ in range(number_of_players)]
    for i in range(can_dealt):
        result[i % number_of_players].append(cards[0])
        cards.pop(0)
    return result

example1 = ['2S', '3S', '4S', '5S', '6S']
print(deal(0, 15, example1))