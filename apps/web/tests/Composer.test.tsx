import { describe, expect, it, vi } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Composer } from '@/features/sessions/Composer';
import { renderWithProviders } from './utils';

describe('Composer', () => {
  it('submits a typed action and clears the field', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    renderWithProviders(<Composer suggestions={[]} pending={false} onSubmit={onSubmit} />);

    const input = screen.getByPlaceholderText(/type anything/i);
    await user.type(input, 'I open the door');
    await user.click(screen.getByRole('button', { name: /send/i }));

    expect(onSubmit).toHaveBeenCalledExactlyOnceWith('I open the door');
    expect(input).toHaveValue('');
  });

  it('trims whitespace and refuses a blank action', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    renderWithProviders(<Composer suggestions={[]} pending={false} onSubmit={onSubmit} />);

    await user.type(screen.getByPlaceholderText(/type anything/i), '   ');
    expect(screen.getByRole('button', { name: /send/i })).toBeDisabled();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('prevents a duplicate submit while a turn is pending', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    const { rerender } = renderWithProviders(
      <Composer suggestions={[]} pending={false} onSubmit={onSubmit} />,
    );

    await user.type(screen.getByPlaceholderText(/type anything/i), 'I wait');
    rerender(<Composer suggestions={[]} pending={true} onSubmit={onSubmit} />);

    expect(screen.getByRole('button', { name: /send/i })).toBeDisabled();
    expect(screen.getByPlaceholderText(/type anything/i)).toBeDisabled();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('submits a suggested action when its button is pressed', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    renderWithProviders(
      <Composer
        suggestions={['Ask Elena what she wants', 'Leave quietly']}
        pending={false}
        onSubmit={onSubmit}
      />,
    );

    await user.click(screen.getByRole('button', { name: 'Leave quietly' }));
    expect(onSubmit).toHaveBeenCalledExactlyOnceWith('Leave quietly');
  });

  it('hides suggestions and shows progress while generating', () => {
    renderWithProviders(
      <Composer suggestions={['Ask Elena']} pending={true} onSubmit={vi.fn()} />,
    );

    expect(screen.queryByRole('button', { name: 'Ask Elena' })).not.toBeInTheDocument();
    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('submits on Enter but allows Shift+Enter for a new line', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    renderWithProviders(<Composer suggestions={[]} pending={false} onSubmit={onSubmit} />);

    const input = screen.getByPlaceholderText(/type anything/i);
    await user.type(input, 'line one{Shift>}{Enter}{/Shift}line two');
    expect(onSubmit).not.toHaveBeenCalled();

    await user.type(input, '{Enter}');
    expect(onSubmit).toHaveBeenCalledExactlyOnceWith('line one\nline two');
  });
});
