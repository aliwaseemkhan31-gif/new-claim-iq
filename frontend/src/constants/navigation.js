/**
 * Primary navigation.
 *
 * Grouped rather than a flat list: the sidebar is the product's table of
 * contents, and a fifteen-item flat list stops being scannable.
 *
 * `permission` gates visibility. A user who cannot use a feature should not
 * see a door they cannot open.
 */
export const NAV_GROUPS = [
  {
    id: 'work',
    label: null, // the first group needs no heading
    items: [
      { label: 'Dashboard', icon: 'pi pi-home', to: { name: 'dashboard' } },
      {
        label: 'Projects',
        icon: 'pi pi-folder',
        to: { name: 'projects' },
        match: '/projects',
      },
    ],
  },
  {
    id: 'corpus',
    label: 'Corpus',
    items: [
      {
        label: 'Documents',
        icon: 'pi pi-file',
        to: { name: 'documents' },
        permission: 'document.view',
      },
      {
        label: 'Correspondence',
        icon: 'pi pi-envelope',
        to: { name: 'correspondence' },
        permission: 'correspondence.view',
      },
      {
        label: 'Evidence',
        icon: 'pi pi-paperclip',
        to: { name: 'evidence' },
        permission: 'evidence.view',
      },
      { label: 'Timeline', icon: 'pi pi-calendar', to: { name: 'timeline' } },
    ],
  },
  {
    id: 'analysis',
    label: 'Analysis',
    items: [
      { label: 'Claims', icon: 'pi pi-briefcase', to: { name: 'claims' }, permission: 'claim.view' },
      {
        label: 'AI Workspace',
        icon: 'pi pi-sparkles',
        to: { name: 'ai-workspace' },
        permission: 'ai.query',
      },
      { label: 'Search', icon: 'pi pi-search', to: { name: 'search' } },
      { label: 'Reports', icon: 'pi pi-chart-bar', to: { name: 'reports' }, permission: 'report.view' },
    ],
  },
  {
    id: 'reference',
    label: 'Reference',
    items: [{ label: 'Knowledge Base', icon: 'pi pi-book', to: { name: 'knowledge-base' } }],
  },
  {
    id: 'system',
    label: 'System',
    items: [
      {
        label: 'Administration',
        icon: 'pi pi-shield',
        to: { name: 'administration' },
        permission: 'org.manage',
      },
      { label: 'Settings', icon: 'pi pi-cog', to: { name: 'settings' } },
    ],
  },
]

/** Tabs inside a project workspace. */
export const PROJECT_TABS = [
  { label: 'Overview', name: 'project-overview' },
  { label: 'Documents', name: 'project-documents', permission: 'document.view' },
  { label: 'Claims', name: 'project-claims', permission: 'claim.view' },
  { label: 'Correspondence', name: 'project-correspondence', permission: 'correspondence.view' },
  { label: 'Evidence', name: 'project-evidence', permission: 'evidence.view' },
  { label: 'Timeline', name: 'project-timeline' },
  { label: 'Clauses', name: 'project-clauses' },
  { label: 'AI Analysis', name: 'project-ai', permission: 'ai.query' },
  { label: 'Reports', name: 'project-reports', permission: 'report.view' },
]
