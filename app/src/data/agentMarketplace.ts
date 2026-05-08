// Shared agent template marketplace. Used by the Agents page marketplace
// browser AND by the onboarding wizard to pre-populate a user's custom
// templates based on their chosen persona.

export interface MarketplaceTemplate {
  name: string
  description: string
  icon: string
  model: string
  budget: number
}

export interface MarketplaceCategory {
  id: string
  category: string
  tagline: string
  templates: MarketplaceTemplate[]
}

export const AGENT_MARKETPLACE: MarketplaceCategory[] = [
  {
    id: 'everyone',
    category: 'For everyone',
    tagline: 'General purpose helpers that work for any use case.',
    templates: [
      { name: 'Summarizer', description: 'Summarize documents, articles, or meeting notes into key points.', icon: 'summarize', model: 'sonnet', budget: 2.0 },
      { name: 'Daily Planner', description: 'Review your tasks and create a focused plan for today.', icon: 'today', model: 'sonnet', budget: 2.0 },
      { name: 'Email Drafter', description: 'Draft a clear, friendly email based on your instructions.', icon: 'mail', model: 'sonnet', budget: 2.0 },
      { name: 'Brainstorm', description: 'Turns a problem into 5-8 structured options with tradeoffs and a recommendation. Good for when you are stuck on approach.', icon: 'psychology', model: 'sonnet', budget: 2.0 },
      { name: 'Research', description: 'Takes a question, searches real sources, and delivers a structured summary with citations and one recommended next step.', icon: 'search', model: 'sonnet', budget: 2.0 },
    ],
  },
  {
    id: 'pm',
    category: 'Product managers',
    tagline: 'I ship features and work with teams.',
    templates: [
      { name: 'Competitive Scan', description: 'When you need a market read. Outputs what competitors are shipping, the gap, and one concrete product move.', icon: 'monitor_heart', model: 'sonnet', budget: 3.0 },
      { name: 'PRD', description: 'When you have a feature idea to spec. Outputs a structured PRD ready to share with engineering.', icon: 'article', model: 'sonnet', budget: 3.0 },
      { name: 'Customer Interview Notes', description: 'When you finish an interview. Outputs themes with real quotes, the clearest unmet need, and follow-up questions.', icon: 'record_voice_over', model: 'sonnet', budget: 2.0 },
      { name: 'Launch Checklist', description: "When you're planning a feature launch. Outputs a grouped checklist covering engineering, docs, comms, and rollout.", icon: 'checklist', model: 'sonnet', budget: 2.0 },
      { name: 'Stakeholder Update', description: 'When you need to keep leadership in the loop without a meeting. Outputs a formatted update ready to send.', icon: 'campaign', model: 'sonnet', budget: 2.0 },
    ],
  },
  {
    id: 'engineer',
    category: 'Engineers',
    tagline: 'I write code and debug things.',
    templates: [
      // Code Review is covered by the built-in Review template (alias "Code Review").
      { name: 'Write Tests', description: 'Paste a function or module, get tests for the happy path, edge cases, and failure paths. Names tests by what they assert.', icon: 'bug_report', model: 'sonnet', budget: 2.0 },
      { name: 'Interactive Debug', description: "When the bug is weird and you don't even know what to ask. Asks one question at a time, narrows the cause, lands a minimal fix.", icon: 'bug_report', model: 'sonnet', budget: 2.0 },
      { name: 'Refactor Plan', description: 'When code works but is hard to live with. Outputs a step-by-step refactor plan that keeps behavior identical, each step independently landable.', icon: 'auto_fix_high', model: 'sonnet', budget: 3.0 },
    ],
  },
  {
    id: 'sales',
    category: 'Sales and customer success',
    tagline: 'I talk to customers and close deals.',
    templates: [
      { name: 'Prospect Research', description: 'Before an outreach call, dig into the company and the person. Get a one-page brief with recent news, likely pain points, and three openers.', icon: 'business', model: 'sonnet', budget: 3.0 },
      { name: 'Cold Outreach Draft', description: "When you need a personalized outreach email that doesn't read like a template. Under 120 words, one clear ask, no filler.", icon: 'outgoing_mail', model: 'sonnet', budget: 2.0 },
      { name: 'Call Prep', description: 'Before a customer call, get a one-page brief with fresh research on the company, the people, three discovery questions, and your exact ask.', icon: 'support_agent', model: 'sonnet', budget: 2.0 },
      { name: 'Follow Up', description: 'After a customer call, turn your notes into a recap email with decisions, open questions, and the next step with a specific date.', icon: 'forward_to_inbox', model: 'sonnet', budget: 2.0 },
      { name: 'Objection Handling', description: 'Prep three responses to a customer objection (empathetic, direct, curious) plus the discovery question that beats answering.', icon: 'question_answer', model: 'sonnet', budget: 2.0 },
    ],
  },
  {
    id: 'writer',
    category: 'Writers and creators',
    tagline: 'I publish content.',
    templates: [
      { name: 'Blog Post', description: 'Write a draft blog post from an outline or rough idea.', icon: 'edit_note', model: 'sonnet', budget: 3.0 },
      { name: 'Social Post', description: 'Turn a long post into short, punchy social versions.', icon: 'share', model: 'sonnet', budget: 2.0 },
      { name: 'Headline Generator', description: 'Write 10 headline options for the same piece of content.', icon: 'title', model: 'sonnet', budget: 2.0 },
      { name: 'Proofreader', description: 'Catch typos, grammar issues, and awkward phrasing.', icon: 'spellcheck', model: 'sonnet', budget: 2.0 },
      { name: 'Name Generator', description: 'Come up with names for projects, features, or products.', icon: 'label', model: 'sonnet', budget: 2.0 },
    ],
  },
  {
    id: 'home',
    category: 'Home and family',
    tagline: 'I manage my household and life.',
    templates: [
      { name: 'Meal Planner', description: 'Plan a week of meals based on what is in the fridge.', icon: 'restaurant', model: 'sonnet', budget: 2.0 },
      { name: 'Trip Planner', description: 'Plan a day trip or vacation with budget and time constraints.', icon: 'flight_takeoff', model: 'sonnet', budget: 3.0 },
      { name: 'Gift Finder', description: 'Suggest gift ideas for a specific person, budget, and occasion.', icon: 'redeem', model: 'sonnet', budget: 2.0 },
      { name: 'Homework Helper', description: 'Walk a kid through a tricky homework problem step by step.', icon: 'school', model: 'sonnet', budget: 2.0 },
    ],
  },
  {
    id: 'student',
    category: 'Students',
    tagline: 'I am studying.',
    templates: [
      { name: 'Study Guide', description: 'Turn class notes into a study guide with key concepts and example questions.', icon: 'menu_book', model: 'sonnet', budget: 2.0 },
      { name: 'Essay Outline', description: 'Build an outline for a paper based on a prompt or topic.', icon: 'format_list_numbered', model: 'sonnet', budget: 2.0 },
      { name: 'Citation Helper', description: 'Format sources in APA, MLA, or Chicago style.', icon: 'format_quote', model: 'sonnet', budget: 2.0 },
    ],
  },
]

// Icon used on the persona card in onboarding
export const PERSONA_ICONS: Record<string, string> = {
  everyone: 'groups',
  pm: 'design_services',
  engineer: 'code',
  sales: 'handshake',
  writer: 'edit_note',
  home: 'home',
  student: 'school',
}
