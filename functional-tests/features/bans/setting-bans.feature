Feature: The bot assists with setting bans

    Scenario: Recording an account ban

        Given a user is in the channel
            And a chanop is in the channel

        When the chanop sets an account ban for the user

        Then the bot will send the chanop a link to update the ban details
            And the ban will show as active in the webui.


    Scenario: Recording a mask ban and flipping it

        Given a user is in the channel
            And a chanop is in the channel

        When the chanop sets a mask ban for the user

        Then the bot asks the chanop if they want the ban to be flipped
            And the chanop says yes
        Then the bot will unset the mask ban 
            And set a corresponding account ban
        Then the bot will send the chanop a link to update the ban details
            And the ban will show as active in the webui.
