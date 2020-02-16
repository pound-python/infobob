Feature: The bot re-indents code when requested

    Scenario: Re-indenting oneliner code

        Given Alice is in the channel
            And Bob is in the channel

        When Alice tells the bot "redent Bob for x in range(5): print(x)"

        Then the bot parses the code
            And the bot reformats the code with indentation
            And the bot uploads the refomatted code to a pastebin
            Then the bot gives Bob a link containing the reformatted code
