interface Tab {
  key: string;
  label: string;
}

interface TopNavTabsProps {
  tabs: Tab[];
  active: string;
  onChange: (key: string) => void;
}

export default function TopNavTabs({ tabs, active, onChange }: TopNavTabsProps) {
  return (
    <div className="flex border-b border-slate-200 dark:border-slate-800 mb-6">
      {tabs.map((tab) => (
        <button
          key={tab.key}
          onClick={() => onChange(tab.key)}
          className={`px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px ${
            active === tab.key
              ? 'border-blue-500 text-blue-600 dark:text-blue-400'
              : 'border-transparent text-slate-600 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200 hover:border-slate-300 dark:hover:border-slate-600'
          }`}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
