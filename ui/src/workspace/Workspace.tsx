import { useEffect, useMemo, useState } from 'react';
import { Link, Navigate, useParams, useSearchParams } from 'react-router-dom';
import {
  ArrowLeft,
  ArrowUp,
  ChevronDown,
  ChevronRight,
  Plus,
  RefreshCw,
  Save,
  Settings2,
  SlidersHorizontal,
  Trash2,
  Undo2,
} from 'lucide-react';
import { api, artifactPath, projectPath } from '../api';
import { ActionIcon, Button } from '../components/Button';
import { Select } from '../components/Select';
import { CardEditor } from './CardEditor';
import { CardContent } from './CardContent';
import { CardFrame, type RecordState } from './CardFrame';
import { newCard } from './cards';
import { useObservations, useRun, useRuns } from './data';
import { WorkspaceDialog } from './Dialog';
import { defaultLayout, snapWidth, uniformSections } from './layout';
import { discover, uid, type Card, type Layout, type Section } from './model';
import { RunPanel } from './RunPanel';
import { matchingRuns } from './runs';
import { readSaved, storageKey, type SavedWorkspace } from './storage';
import './workspace.css';

export function WorkspacePage() {
  const { project = '' } = useParams();
  return <Workspace key={project} project={project} />;
}

// Keep links from the initial run-workspace UI pointing at the run's Workspace tab.
export function RunWorkspaceRedirect() {
  const { project = '', runId = '' } = useParams();
  const [params] = useSearchParams();
  const query = new URLSearchParams(params);
  query.set('tab', 'workspace');
  return <Navigate replace to={`${artifactPath(project, runId)}?${query}`} />;
}

type Editing = { card: Card; creating: boolean; changed?: boolean };
type Naming = { kind: 'save' | 'section' | 'rename'; value: string; id?: string };

export function Workspace({ project, runId }: { project: string; runId?: string }) {
  const Surface = runId ? 'div' : 'main';
  const [params, setParams] = useSearchParams();
  const [layout, setLayout] = useState(() => uniformSections(defaultLayout(params.get('example') === 'cards')));
  const [saved, setSaved] = useState<Layout[]>([]);
  const [key, setKey] = useState('');
  const [storageError, setStorageError] = useState('');
  const [loaded, setLoaded] = useState(false);
  const [history, setHistory] = useState<Layout[]>([]);
  const [revision, setRevision] = useState(0);
  const [showRuns, setShowRuns] = useState(false);
  const [editor, setEditor] = useState<Editing>();
  const [expanded, setExpanded] = useState<Card>();
  const [naming, setNaming] = useState<Naming>();
  const [dragged, setDragged] = useState<string>();

  // Saved layouts live in this browser, per repository and project.
  useEffect(() => {
    let closed = false;
    api<{ repository_id: string }>('/ui-api/session')
      .then((session) => {
        if (closed) return;
        const nextKey = storageKey(session.repository_id, project);
        try {
          const stored = readSaved(nextKey);
          setKey(nextKey);
          if (stored) {
            setSaved(stored.layouts);
            const active = stored.layouts.find((l) => l.id === stored.active) || stored.layouts[0] || defaultLayout();
            if (!params.has('example')) setLayout(uniformSections(active));
          }
        } finally {
          setLoaded(true);
        }
      })
      .catch((error) => {
        if (closed) return;
        setStorageError(String(error));
        setLoaded(true);
      });
    return () => {
      closed = true;
    };
  }, [project]);

  const runs = useRuns(project, revision, !runId);
  const focused = useRun(project, runId, revision);
  const candidates = useMemo(() => matchingRuns(runs.items, layout), [runs.items, layout]);
  const selected = useMemo(() => {
    if (runId) return focused.run ? [focused.run] : [];
    return candidates.filter((run) => layout.selected === null || layout.selected.includes(run.id));
  }, [runId, focused.run, candidates, layout.selected]);
  const data = useObservations(project, loaded ? selected : [], revision);
  const fields = useMemo(() => discover(data.rows), [data.rows]);
  const scopeLoading = runId ? focused.loading : runs.loading;
  const scopeError = runId ? focused.error : runs.error;
  const records: RecordState = {
    rows: data.rows,
    loading: scopeLoading || data.loading,
    failed: !!(scopeError || data.error),
  };
  const hiddenCount = candidates.length - selected.length;
  const dirty = JSON.stringify(saved.find((l) => l.id === layout.id)) !== JSON.stringify(layout);

  function change(next: Layout, remember = true) {
    if (remember) setHistory((h) => [...h.slice(-29), layout]);
    setLayout(uniformSections(next));
  }
  const patch = (value: Partial<Layout>) => change({ ...layout, ...value });

  function store(value: SavedWorkspace) {
    localStorage.setItem(key, JSON.stringify(value));
  }
  function persist(next: Layout) {
    if (!key) return;
    try {
      const layouts = [...saved.filter((l) => l.id !== next.id), next];
      store({ version: 1, active: next.id, layouts });
      setSaved(layouts);
      setLayout(next);
      setStorageError('');
      if (params.has('example')) {
        const query = new URLSearchParams(params);
        query.delete('example');
        setParams(query, { replace: true });
      }
    } catch (error) {
      setStorageError(`Could not save this layout: ${String(error)}`);
    }
  }
  function open(value: string) {
    if (value === '__new') {
      const sections = [{ id: uid(), title: 'Overview', collapsed: false }];
      change({ ...defaultLayout(), name: 'Untitled workspace', sections, cards: [] });
      return;
    }
    const next = saved.find((l) => l.id === value);
    if (!next) return;
    change(next);
    try {
      if (key) store({ version: 1, active: next.id, layouts: saved });
    } catch (error) {
      setStorageError(String(error));
    }
  }

  // The first card of a section sets the size of every card in it.
  const sectionSize = (section: string) => {
    const card = layout.cards.find((c) => c.section === section);
    return card ? { width: card.width, height: card.height } : {};
  };
  const create = (section: string) =>
    setEditor({ card: { ...newCard('scalar', section), ...sectionSize(section) }, creating: true });
  const cardsOf = (section: string) => layout.cards.filter((c) => c.section === section);

  function move(id: string, delta: number) {
    const cards = [...layout.cards];
    const index = cards.findIndex((c) => c.id === id);
    let target = index + delta;
    while (target >= 0 && target < cards.length && cards[target].section !== cards[index].section) target += delta;
    if (target < 0 || target >= cards.length) return;
    [cards[index], cards[target]] = [cards[target], cards[index]];
    patch({ cards });
  }
  function drop(section: string, before?: string) {
    if (!dragged || dragged === before) return;
    const card = layout.cards.find((c) => c.id === dragged)!;
    const cards = layout.cards.filter((c) => c.id !== dragged);
    const index = before ? cards.findIndex((c) => c.id === before) : cards.length;
    cards.splice(index, 0, { ...card, section, ...sectionSize(section) });
    patch({ cards });
    setDragged(undefined);
  }
  function sizeSection(section: string, width: number, height: number) {
    patch({ cards: layout.cards.map((c) => (c.section === section ? { ...c, width: snapWidth(width), height } : c)) });
  }
  function apply(card: Card) {
    if (!editor) return;
    const cards = editor.creating ? [...layout.cards, card] : layout.cards.map((c) => (c.id === card.id ? card : c));
    const sized = cards.map((c) => (c.section === card.section ? { ...c, width: card.width, height: card.height } : c));
    const next = uniformSections({ ...layout, cards: sized });
    change(next, !editor.changed);
    persist(next);
    setEditor(editor.creating ? undefined : { ...editor, changed: true });
  }

  function updateSection(id: string, value: Partial<Section>) {
    patch({ sections: layout.sections.map((s) => (s.id === id ? { ...s, ...value } : s)) });
  }
  function raiseSection(index: number) {
    const sections = [...layout.sections];
    [sections[index - 1], sections[index]] = [sections[index], sections[index - 1]];
    patch({ sections });
  }
  function removeSection(id: string) {
    const target = layout.sections.find((s) => s.id !== id)!.id;
    patch({
      sections: layout.sections.filter((s) => s.id !== id),
      cards: layout.cards.map((c) => (c.section === id ? { ...c, section: target, ...sectionSize(target) } : c)),
    });
  }
  function name(e: React.FormEvent) {
    e.preventDefault();
    if (!naming) return;
    const value = naming.value.trim();
    if (!value) return;
    if (naming.kind === 'save') persist({ ...layout, id: uid(), name: value });
    else if (naming.kind === 'section')
      patch({ sections: [...layout.sections, { id: uid(), title: value, collapsed: false }] });
    else updateSection(naming.id!, { title: value });
    setNaming(undefined);
  }

  const status = records.loading
    ? `Loading complete record streams…${data.loaded ? ` ${data.loaded.toLocaleString()} records loaded` : ''}`
    : `${selected.length} ${selected.length === 1 ? 'run' : 'runs'} · ${data.rows.length.toLocaleString()} records` +
      (!runId && hiddenCount ? ` · ${hiddenCount} hidden` : '');
  const live = !data.loading && selected.some((r) => r.state !== 'finished');
  const layoutChoices = [
    ...(saved.some((l) => l.id === layout.id) ? [] : [{ value: layout.id, label: layout.name }]),
    ...saved.map((l) => ({ value: l.id, label: l.name })),
    { value: '__new', label: '+ New workspace' },
  ];
  const addCard = (
    <Button small={!!runId} primary onClick={() => create(layout.sections[0].id)}>
      <Plus size={16} /> Add card
    </Button>
  );

  return (
    <Surface className={`workspace-page ${runId ? 'workspace-embedded' : ''}`}>
      {runId ? (
        <div className="workspace-run-breadcrumb">
          <Link to={`${projectPath(project)}/workspace`}>
            <ArrowLeft size={14} /> Project workspace
          </Link>
        </div>
      ) : (
        <header className="workspace-heading">
          <div>
            <span className="eyebrow">{project} / Workspace</span>
            <h1>Watch the work unfold.</h1>
            <p>Explore records as they arrive. Arrange the views that matter.</p>
          </div>
          {addCard}
        </header>
      )}

      <div className="workspace-toolbar">
        <div className="workspace-view-select">
          <span>Layout</span>
          <Select label="Saved layout" value={layout.id} disabled={!loaded} choices={layoutChoices} onChange={open} />
        </div>
        <span className="workspace-save-status">{dirty ? 'Unsaved changes' : 'Saved in this browser'}</span>
        <Button small disabled={!key || !dirty} onClick={() => persist(layout)}>
          <Save size={14} /> Save
        </Button>
        <Button small disabled={!key} onClick={() => setNaming({ kind: 'save', value: `${layout.name} copy` })}>
          Save as…
        </Button>
        <span className="workspace-toolbar-spacer" />
        {runId ? (
          addCard
        ) : (
          <Button
            small
            className="workspace-run-toggle"
            aria-expanded={showRuns}
            onClick={() => setShowRuns(!showRuns)}
          >
            <SlidersHorizontal size={14} /> {selected.length} runs
          </Button>
        )}
        <ActionIcon
          label="Undo layout change"
          disabled={!history.length}
          onClick={() => {
            persist(history[history.length - 1]);
            setHistory((h) => h.slice(0, -1));
          }}
        >
          <Undo2 size={16} />
        </ActionIcon>
        <ActionIcon label="Refresh workspace" onClick={() => setRevision((n) => n + 1)}>
          <RefreshCw size={16} />
        </ActionIcon>
      </div>
      {storageError && (
        <p className="workspace-error" role="alert">
          {storageError}
        </p>
      )}

      <div className={`workspace-body ${runId ? 'single-run' : ''}`}>
        {!runId && (
          <RunPanel
            project={project}
            layout={layout}
            candidates={candidates}
            selected={selected}
            open={showRuns}
            patch={patch}
          />
        )}
        <div className="workspace-canvas">
          <div className="workspace-data-status" role="status">
            {status}
            {live ? ' · Live' : ''}
            <span>{runId ? 'Project panels · this run’s data' : 'Changes to cards affect this view only.'}</span>
          </div>
          {records.failed && (
            <p className="workspace-error" role="alert">
              {String(scopeError || data.error)}
            </p>
          )}
          {!scopeLoading && !selected.length && !scopeError && (
            <p className="workspace-empty">No runs visible. Use the eye controls to display runs.</p>
          )}

          {layout.sections.map((section, index) => (
            <section
              className="workspace-section"
              key={section.id}
              onDragOver={(e) => {
                if (dragged) e.preventDefault();
              }}
              onDrop={(e) => {
                e.preventDefault();
                drop(section.id);
              }}
            >
              <header className="workspace-section-heading">
                <button
                  className="workspace-section-toggle"
                  aria-expanded={!section.collapsed}
                  onClick={() => updateSection(section.id, { collapsed: !section.collapsed })}
                >
                  {section.collapsed ? <ChevronRight size={18} /> : <ChevronDown size={18} />}
                  <h2>{section.title}</h2>
                  <span>{cardsOf(section.id).length}</span>
                </button>
                <div className="workspace-section-actions">
                  <ActionIcon
                    label={`Rename ${section.title}`}
                    onClick={() => setNaming({ kind: 'rename', value: section.title, id: section.id })}
                  >
                    <Settings2 size={15} />
                  </ActionIcon>
                  <ActionIcon
                    label={`Move ${section.title} up`}
                    disabled={index === 0}
                    onClick={() => raiseSection(index)}
                  >
                    <ArrowUp size={15} />
                  </ActionIcon>
                  <ActionIcon
                    label={`Remove ${section.title}`}
                    disabled={layout.sections.length === 1}
                    onClick={() => removeSection(section.id)}
                  >
                    <Trash2 size={15} />
                  </ActionIcon>
                  <ActionIcon label={`Add card to ${section.title}`} onClick={() => create(section.id)}>
                    <Plus size={16} />
                  </ActionIcon>
                </div>
              </header>
              {!section.collapsed && (
                <div className="workspace-grid">
                  {cardsOf(section.id).map((card) => (
                    <CardFrame
                      key={card.id}
                      card={card}
                      records={records}
                      dragging={!!dragged}
                      actions={{
                        settings: () => setEditor({ card, creating: false }),
                        expand: () => setExpanded(card),
                        move: (delta) => move(card.id, delta),
                        duplicate: () =>
                          patch({ cards: [...layout.cards, { ...card, id: uid(), title: `${card.title} copy` }] }),
                        remove: () => patch({ cards: layout.cards.filter((c) => c.id !== card.id) }),
                        size: (width, height) => sizeSection(card.section, width, height),
                        drag: setDragged,
                        drop: () => drop(section.id, card.id),
                      }}
                    />
                  ))}
                  {!cardsOf(section.id).length && (
                    <button className="workspace-empty-section" onClick={() => create(section.id)}>
                      + Add a card to this section
                    </button>
                  )}
                </div>
              )}
            </section>
          ))}
          <Button onClick={() => setNaming({ kind: 'section', value: 'New section' })}>
            <Plus size={16} /> Add section
          </Button>
        </div>
      </div>

      {editor && (
        <CardEditor
          initial={editor.card}
          creating={editor.creating}
          fields={fields}
          rows={data.rows}
          onClose={() => setEditor(undefined)}
          onApply={apply}
        />
      )}
      {expanded && (
        <WorkspaceDialog title={expanded.title} onClose={() => setExpanded(undefined)} wide>
          <div className="workspace-expanded-card">
            <CardContent card={expanded} rows={data.rows} />
          </div>
        </WorkspaceDialog>
      )}
      {naming && (
        <WorkspaceDialog
          title={{ save: 'Save layout as', rename: 'Rename section', section: 'Add section' }[naming.kind]}
          onClose={() => setNaming(undefined)}
        >
          <form onSubmit={name}>
            <label className="workspace-field">
              <span>Name</span>
              <input autoFocus value={naming.value} onChange={(e) => setNaming({ ...naming, value: e.target.value })} />
            </label>
            <footer className="workspace-dialog-footer">
              <Button onClick={() => setNaming(undefined)}>Cancel</Button>
              <Button primary type="submit" disabled={!naming.value.trim()}>
                {naming.kind === 'save' ? 'Save layout' : 'Apply'}
              </Button>
            </footer>
          </form>
        </WorkspaceDialog>
      )}
    </Surface>
  );
}
