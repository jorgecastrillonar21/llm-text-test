import { useState, type FormEvent } from 'react';
import { useI18n } from '@/i18n/useI18n';
import { useCreateCharacter } from '@/api/hooks';
import { Button, Card, Field, TextArea, TextInput } from '@/components/ui';

export function CreateCharacterForm({
  worldId,
  onDone,
}: {
  worldId: string;
  onDone: () => void;
}) {
  const { t } = useI18n();
  const createCharacter = useCreateCharacter(worldId);

  const [name, setName] = useState('');
  const [personality, setPersonality] = useState('');
  const [description, setDescription] = useState('');
  const [speechStyle, setSpeechStyle] = useState('');

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim() || createCharacter.isPending) return;
    await createCharacter.mutateAsync({
      name: name.trim(),
      personality: personality.trim(),
      description: description.trim(),
      speech_style: speechStyle.trim(),
    });
    onDone();
  }

  return (
    <Card>
      <form onSubmit={handleSubmit} className="flex flex-col gap-4">
        <Field label={t('character.form.name')}>
          {(id) => (
            <TextInput
              id={id}
              value={name}
              required
              onChange={(e) => setName(e.target.value)}
            />
          )}
        </Field>

        <Field label={t('character.form.personality')}>
          {(id) => (
            <TextInput
              id={id}
              value={personality}
              placeholder={t('character.form.personalityPlaceholder')}
              onChange={(e) => setPersonality(e.target.value)}
            />
          )}
        </Field>

        <Field label={t('character.form.description')}>
          {(id) => (
            <TextArea
              id={id}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          )}
        </Field>

        <Field label={t('character.form.speechStyle')}>
          {(id) => (
            <TextInput
              id={id}
              value={speechStyle}
              onChange={(e) => setSpeechStyle(e.target.value)}
            />
          )}
        </Field>

        {createCharacter.isError ? (
          <p role="alert" className="text-sm text-red-300">
            {createCharacter.error.message}
          </p>
        ) : null}

        <div className="flex gap-2">
          <Button type="submit" disabled={createCharacter.isPending || !name.trim()}>
            {createCharacter.isPending ? t('common.creating') : t('common.create')}
          </Button>
          <Button type="button" variant="ghost" onClick={onDone}>
            {t('common.cancel')}
          </Button>
        </div>
      </form>
    </Card>
  );
}
