import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router';
import { useI18n } from '@/i18n/useI18n';
import { useCreateWorld } from '@/api/hooks';
import { Button, Card, Field, TextArea, TextInput } from '@/components/ui';
import type { Language } from '@/api/types';

const LANGUAGE_OPTIONS: { value: Language; label: string }[] = [
  { value: 'en', label: 'English' },
  { value: 'es', label: 'Español' },
];

export function CreateWorldForm({ onDone }: { onDone: () => void }) {
  const { t, locale } = useI18n();
  const navigate = useNavigate();
  const createWorld = useCreateWorld();

  const [name, setName] = useState('');
  const [genre, setGenre] = useState('');
  const [setting, setSetting] = useState('');
  const [description, setDescription] = useState('');
  // Default the story language to the interface language: it is the best guess,
  // and it is fixed once the world is created.
  const [language, setLanguage] = useState<Language>(locale);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim() || createWorld.isPending) return;
    const world = await createWorld.mutateAsync({
      name: name.trim(),
      genre: genre.trim(),
      setting: setting.trim(),
      description: description.trim(),
      language,
    });
    onDone();
    navigate(`/worlds/${world.id}`);
  }

  return (
    <Card>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <Field label={t('world.form.name')}>
          {(id) => (
            <TextInput
              id={id}
              value={name}
              required
              placeholder={t('world.form.namePlaceholder')}
              onChange={(e) => setName(e.target.value)}
            />
          )}
        </Field>

        <Field label={t('world.form.language')} hint={t('world.form.languageHelp')}>
          {(id) => (
            <select
              id={id}
              value={language}
              onChange={(e) => setLanguage(e.target.value as Language)}
              className="w-full rounded-lg border border-ink-700 bg-ink-850 px-3 py-2.5 text-base text-ink-50 focus:border-arcane-400 focus:outline-none"
            >
              {LANGUAGE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          )}
        </Field>

        <Field label={t('world.form.genre')}>
          {(id) => (
            <TextInput
              id={id}
              value={genre}
              placeholder={t('world.form.genrePlaceholder')}
              onChange={(e) => setGenre(e.target.value)}
            />
          )}
        </Field>

        <Field label={t('world.form.setting')}>
          {(id) => (
            <TextArea
              id={id}
              value={setting}
              placeholder={t('world.form.settingPlaceholder')}
              onChange={(e) => setSetting(e.target.value)}
            />
          )}
        </Field>

        <Field label={t('world.form.description')}>
          {(id) => (
            <TextArea
              id={id}
              value={description}
              placeholder={t('world.form.descriptionPlaceholder')}
              onChange={(e) => setDescription(e.target.value)}
            />
          )}
        </Field>

        {createWorld.isError ? (
          <p role="alert" className="text-sm text-red-300">
            {createWorld.error.message}
          </p>
        ) : null}

        <div className="flex gap-2">
          <Button type="submit" disabled={createWorld.isPending || !name.trim()}>
            {createWorld.isPending ? t('common.creating') : t('common.create')}
          </Button>
          <Button type="button" variant="ghost" onClick={onDone}>
            {t('common.cancel')}
          </Button>
        </div>
      </form>
    </Card>
  );
}
