The basic idea

Collectivize keeps a map of who and what exists in a community, and how they relate. The things on the map are nodes, and the relationships between them are edges. Everything lives in one connected map rather than separate maps for people, orgs, and events, so nothing has to be copied or kept in sync.

The things (nodes)
People: real humans with accounts.
Organizations: chapters, nationals, councils, clubs, and later local groups and businesses.
Events: a specific gathering at a specific time.
Event series: a recurring pattern like weekly meetings, so an org can post a semester of events at once.
Tags: topics like philanthropy or robotics, used for search and for explaining recommendations.
The relationships (edges)
A person is a member of an org. The person claims it and the org confirms it.
An org is part of another org, like a chapter under its national or a council. Both sides must agree, and the hierarchy can’t loop back on itself.
An org hosts an event. Co-hosts have to accept being listed.
A person RSVPs to an event before it happens and attended it afterward.
A person follows an org to stay updated without joining.
Two people are connected only if both agree.
The rules everything follows

Every fact says where it came from. The system knows whether something was submitted by an org, imported, or entered by staff, and when it was last confirmed. Unverified or outdated information is labeled and ranked lower, so it never looks as trustworthy as confirmed information.

You control who sees your facts. Each membership, RSVP, and connection has its own visibility setting. When a fact involves two parties, the stricter setting wins, so nobody can make you more visible than you chose.

Seeing and using are separate. Apart from who can see a fact, you also decide whether the system can use it to make recommendations. By default, your data only improves your own recommendations and is never used to show you to others.

Nothing disappears by accident. Removing something archives it first, while deleting your account truly erases your data.

What the system deliberately doesn’t store

“Similar to” is never saved as a relationship. The system describes each person, org, and event with a profile of numbers (an embedding) and computes similarity on demand when it needs it. Nobody, including organizations, can look up “who is similar to this person.” This keeps the system from building a hidden social graph that no one agreed to.

How search works

When you search something like “UTK football events”:

Understand the question. An AI turns the words into filters (UTK, upcoming, football) and a description of what you mean.
Find matching events. Keyword search catches exact terms and names, and meaning-based search catches related wording.
Rank by who will be there. Each event is scored by whether there are enough people you’d want to meet, using your best matches rather than an average so big events don’t drown out small ones. Friends attending, friends of friends, and shared orgs also count.
Show why. Each result says why it ranked highly, using only information people allowed to be shown. Small numbers that could identify someone are hidden.

The system aims to get you to events, not to keep you on the app. It learns from real attendance and return visits rather than clicks, and it deliberately includes events where you’d meet new people rather than only your existing circle.

How the network view works
Tapping a connection shows their network with them at the center: their direct connections and their organizations.
Their orgs act as hubs, and their connections cluster near the orgs they share, which shows the shape of someone’s life without drawing every relationship between other people.
You only see the parts each person has allowed you to see, so two people viewing the same profile may see different maps.
You can open the full view only for your own connections, so nobody can click their way across the entire network.
The view highlights what you share with the person and which of their communities you haven’t joined yet, which is where discovery happens.
What gets built first

The Greek-life pilot only needs orgs, events, event series, tags, and admin accounts, plus the provenance and visibility rules. Student profiles, RSVPs, connections, recommendations, and the network view are already designed into the map, so they can be added later without rebuilding anything.