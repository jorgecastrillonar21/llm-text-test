import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router';
import { useI18n } from '@/i18n/useI18n';
import { useCreateSession } from '@/api/hooks';
import { Button, Card, Field, TextArea, TextInput } from '@/components/ui';

export function StartSessionForm({ worldId, onDone }: { worldId: string; onDone: () => void }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const createSession = useCreateSession();

  const [title, setTitle] = useState('');
  const [playerName, setPlayerName] = useState('');
  const [playerDescription, setPlayerDescription] = useState('');
  const [location, setLocation] = useState('');

  const canSubmit = title.trim() !== '' && playerName.trim() !== '';

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!canSubmit || createSession.isPending) return;
    const session = await createSession.mutateAsync({
      world_id: worldId,
      title: title.trim(),
      player_name: playerName.trim(),
      player_description: playerDescription.trim(),
      current_location: location.trim(),
    });
    onDone();
    navigate(`/sessions/${session.id}`);
  }

  return (
    <Card>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <Field label={t('session.form.title')}>
          {(id) => (
            <TextInput id={id} value={title} required onChange={(e) => setTitle(e.target.value)} />
          )}
        </Field>

        <Field label={t('session.form.playerName')}>
          {(id) => (
            <TextInput
              id={id}
              value={playerName}
              required
              onChange={(e) => setPlayerName(e.target.value)}
            />
          )}
        </Field>

        <Field label={t('session.form.playerDescription')}>
          {(id) => (
            <TextArea
              id={id}
              value={playerDescription}
              onChange={(e) => setPlayerDescription(e.target.value)}
            />
          )}
        </Field>

        <Field label={t('session.form.location')}>
          {(id) => (
            <TextInput id={id} value={location} onChange={(e) => setLocation(e.target.value)} />
          )}
        </Field>

        {createSession.isError ? (
          <p role="alert" className="text-sm text-red-300">
            {createSession.error.message}
          </p>
        ) : null}

        <div className="flex gap-2">
          <Button type="submit" disabled={createSession.isPending || !canSubmit}>
            {createSession.isPending ? t('common.creating') : t('world.startSession')}
          </Button>
          <Button type="button" variant="ghost" onClick={onDone}>
            {t('common.cancel')}
          </Button>
        </div>
      </form>
    </Card>
  );
}
