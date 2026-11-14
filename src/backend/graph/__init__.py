"""Graph services that enforce the rules in user_Orgs.md."""

from typing import Any

from .activity import ActivityService
from .ai_search import AiSearchResult, AiSearchService, RequestInterpreter
from .calendar import CalendarService, HostEvents
from .claude_interpreter import ClaudeInterpreter
from .cohosting import CoHostingService
from .connections import ConnectionService
from .data_access import DataAccessService, MlDataset
from .discovery import DiscoveryPage, DiscoveryQuery, DiscoveryService, RequestMatch
from .errors import NotFoundError, PermissionDeniedError
from .events import EventService
from .hierarchy import ALLOWED_PART_OF, HierarchyService
from .integrity import IntegrityIssue, IntegrityService
from .invites import DEFAULT_EMAIL_DOMAINS, InviteService, IssuedInvite, Redemption
from .membership import MembershipService
from .my_calendar import PersonalCalendar, PersonalCalendarService, Relation
from .network import NetworkService
from .preferences import PreferenceService, TasteProfile
from .privacy import PrivacyService
from .profiles import ProfileAccess, ProfileService, ProfileView
from .ranking import DEFAULT_WEIGHTS, RankingWeights
from .roster import DEFAULT_ROSTER_POLICY, RosterEntry, RosterPolicy, RosterService
from .rsvps import RsvpService
from .search_agent import AgentSearchResult, SearchAgentService
from .series import SeriesService
from .stars import StarService
from .store import GraphStore, InMemoryGraphStore
from .visibility import VisibilityService


class CollectivizeGraph:
    """Facade wiring the store and every rule-enforcing service together."""

    def __init__(
        self,
        store: GraphStore | None = None,
        *,
        roster_policy: RosterPolicy = DEFAULT_ROSTER_POLICY,
        invite_email_domains: tuple[str, ...] = DEFAULT_EMAIL_DOMAINS,
        ranking_weights: RankingWeights = DEFAULT_WEIGHTS,
        search_interpreter: RequestInterpreter | None = None,
        agent_client: Any | None = None,
    ) -> None:
        self.store: GraphStore = store or InMemoryGraphStore()
        self.membership = MembershipService(self.store)
        self.connections = ConnectionService(self.store)
        self.roster = RosterService(
            self.store, self.membership, self.connections, roster_policy
        )
        self.invites = InviteService(self.store, self.connections, invite_email_domains)
        self.hierarchy = HierarchyService(self.store, self.membership)
        self.events = EventService(self.store, self.membership)
        self.series = SeriesService(self.store, self.membership, self.events)
        self.privacy = PrivacyService(
            self.store, self.membership, self.events, self.connections
        )
        self.preferences = PreferenceService(self.store)
        self.rsvps = RsvpService(
            self.store, self.privacy, self.connections, self.preferences
        )
        self.discovery = DiscoveryService(
            self.store,
            self.events,
            self.hierarchy,
            self.privacy,
            self.connections,
            self.rsvps,
            self.preferences,
            ranking_weights,
        )
        self.co_hosting = CoHostingService(self.store, self.events, self.privacy)
        self.calendar = CalendarService(self.store, self.events, self.privacy)
        self.stars = StarService(self.store, self.privacy)
        self.my_calendar = PersonalCalendarService(
            self.store, self.events, self.privacy, self.stars
        )
        self.network = NetworkService(self.store, self.privacy, self.roster)
        self.profiles = ProfileService(
            self.store, self.privacy, self.connections, self.roster, self.calendar, self.rsvps
        )
        self.activity = ActivityService(self.store)
        self.ai_search = AiSearchService(
            self.store,
            self.discovery,
            self.preferences,
            self.activity,
            search_interpreter or ClaudeInterpreter(),  # connects only when used
        )
        self.data_access = DataAccessService(self.store, self.preferences, self.activity)
        self.search_agent = SearchAgentService(
            self.store,
            self.discovery,
            self.preferences,
            self.activity,
            self.privacy,
            fallback=self.ai_search,
            client=agent_client,  # None: a real client is made on first use
        )
        self.integrity = IntegrityService(self.store)
        self.visibility = VisibilityService(self.store, self.connections, self.membership)


__all__ = [
    "ALLOWED_PART_OF",
    "DEFAULT_EMAIL_DOMAINS",
    "DEFAULT_ROSTER_POLICY",
    "DEFAULT_WEIGHTS",
    "ActivityService",
    "AgentSearchResult",
    "AiSearchResult",
    "AiSearchService",
    "CalendarService",
    "ClaudeInterpreter",
    "CoHostingService",
    "CollectivizeGraph",
    "ConnectionService",
    "DataAccessService",
    "DiscoveryPage",
    "DiscoveryQuery",
    "DiscoveryService",
    "EventService",
    "GraphStore",
    "HierarchyService",
    "HostEvents",
    "InMemoryGraphStore",
    "IntegrityIssue",
    "IntegrityService",
    "InviteService",
    "IssuedInvite",
    "MembershipService",
    "MlDataset",
    "NetworkService",
    "NotFoundError",
    "PermissionDeniedError",
    "PersonalCalendar",
    "PersonalCalendarService",
    "PreferenceService",
    "PrivacyService",
    "ProfileAccess",
    "ProfileService",
    "ProfileView",
    "RankingWeights",
    "Redemption",
    "Relation",
    "RequestInterpreter",
    "RequestMatch",
    "RosterEntry",
    "RosterPolicy",
    "RosterService",
    "RsvpService",
    "SearchAgentService",
    "SeriesService",
    "StarService",
    "TasteProfile",
    "VisibilityService",
]
