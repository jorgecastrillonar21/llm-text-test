/**
 * UI copy. This is the interface chrome only.
 *
 * Story text (narration, dialogue, suggested actions) is generated in the
 * world's own language, which is fixed when the world is created and is never
 * translated here.
 */

export const UI_LOCALES = ['en', 'es'] as const;
export type UiLocale = (typeof UI_LOCALES)[number];

export const LOCALE_LABELS: Record<UiLocale, string> = {
  en: 'English',
  es: 'Español',
};

const en = {
  'app.title': 'Playable Story Engine',
  'app.tagline': 'A local-first AI-driven interactive story.',

  'nav.home': 'Worlds',
  'nav.settings': 'Settings',
  'nav.back': 'Back',

  'common.loading': 'Loading…',
  'common.retry': 'Retry',
  'common.cancel': 'Cancel',
  'common.create': 'Create',
  'common.creating': 'Creating…',
  'common.optional': 'optional',
  'common.language': 'Language',
  'common.error': 'Something went wrong',

  'home.heading': 'Your worlds',
  'home.empty': 'No worlds yet. Create one to begin.',
  'home.createWorld': 'Create a world',
  'home.continue': 'Continue playing',
  'home.sessionsEmpty': 'No saved games yet.',
  'home.openWorld': 'Open world',

  'world.form.name': 'World name',
  'world.form.namePlaceholder': 'The Fractured Crown',
  'world.form.genre': 'Genre',
  'world.form.genrePlaceholder': 'Fantasy / anime / adventure',
  'world.form.setting': 'Setting',
  'world.form.settingPlaceholder': 'A walled capital where the wards are failing.',
  'world.form.description': 'Premise',
  'world.form.descriptionPlaceholder': 'The player wakes in a city on the edge of a crisis.',
  'world.form.language': 'Story language',
  'world.form.languageHelp':
    'The language the story is written in. This is fixed once the world is created.',
  'world.form.preset': 'World style',
  'world.form.presetHelp': 'How harsh, hopeful and fast this world is. Chosen once, at creation.',

  'rules.heading': 'World rules',
  'rules.field.danger': 'Danger',
  'rules.field.lethality': 'Lethality',
  'rules.field.plotArmor': 'Plot armor',
  'rules.field.consequences': 'Consequences',
  'rules.field.progression': 'Progression',
  'rules.field.simulation': 'World clock',
  'rules.field.enforcement': 'Rules',

  'rules.level.veryLow': 'Very low',
  'rules.level.low': 'Low',
  'rules.level.moderate': 'Moderate',
  'rules.level.high': 'High',
  'rules.level.veryHigh': 'Very high',

  'rules.pace.off': 'None',
  'rules.pace.verySlow': 'Very slow',
  'rules.pace.slow': 'Slow',
  'rules.pace.medium': 'Medium',
  'rules.pace.fast': 'Fast',
  'rules.pace.animeFast': 'Explosive',

  'rules.time.paused': 'Paused',
  'rules.time.actionBased': 'Moves with you',
  'rules.time.active': 'Always running',

  'rules.enforcement.cinematic': 'Cinematic',
  'rules.enforcement.flexible': 'Flexible',
  'rules.enforcement.strict': 'Strict',
  'rules.enforcement.simulationist': 'Simulationist',

  'rules.preset.balanced': 'Balanced',
  'rules.preset.cozyFantasy': 'Cozy fantasy',
  'rules.preset.shonen': 'Shonen',
  'rules.preset.darkFantasy': 'Dark fantasy',
  'rules.preset.simulationist': 'Simulationist',
  'rules.preset.isekaiPowerFantasy': 'Isekai power fantasy',

  'world.characters': 'Characters',
  'world.charactersEmpty': 'No characters yet. Add one so the world has someone in it.',
  'world.addCharacter': 'Add character',
  'world.startSession': 'Start a new game',
  'world.sessions': 'Saved games',

  'character.form.name': 'Name',
  'character.form.personality': 'Personality',
  'character.form.personalityPlaceholder': 'intelligent, sarcastic, cautious',
  'character.form.appearance': 'Appearance',
  'character.form.description': 'Description',
  'character.form.speechStyle': 'Speech style',
  'character.form.backstory': 'Backstory',

  'session.form.title': 'Save name',
  'session.form.playerName': 'Your character’s name',
  'session.form.playerDescription': 'Who are you?',
  'session.form.location': 'Starting location',
  'session.turn': 'Turn {n}',
  'session.empty': 'The story has not started. Type what you do first.',
  'session.inputPlaceholder': 'Type anything…',
  'session.send': 'Send',
  'session.sending': 'The story is unfolding…',
  'session.suggestions': 'Suggested actions',
  'session.turnFailed': 'That turn could not be generated.',
  'session.turnFailedRetry': 'Nothing was saved. You can try again.',
  'session.narrator': 'Narrator',
  'session.you': 'You',
  // The fictional clock. Read-only on purpose: the game moves time, never the UI.
  'session.time.label': 'Time in the story',
  'session.time.dawn': 'dawn',
  'session.time.morning': 'morning',
  'session.time.afternoon': 'afternoon',
  'session.time.evening': 'evening',
  'session.time.night': 'night',
  'session.time.lateNight': 'late night',

  'settings.heading': 'Settings',
  'settings.uiLanguage': 'Interface language',
  'settings.uiLanguageHelp': 'Affects this interface only, not the language of your stories.',
  'settings.aiStatus': 'AI status',
  'settings.storyProvider': 'Story provider',
  'settings.imageProvider': 'Image provider',
  'settings.model': 'Model',
  'settings.configuredVia': 'Providers are configured with environment variables on the PC.',

  'status.ready': 'Ready',
  'status.unreachable': 'Unreachable',
  'status.misconfigured': 'Needs configuration',
  'status.disabled': 'Disabled',
  'status.offline': 'Backend unreachable',
  'status.offlineHelp': 'The app cannot reach the API. Is the backend running on your PC?',
} as const;

export type MessageKey = keyof typeof en;

const es: Record<MessageKey, string> = {
  'app.title': 'Motor de Historias Jugables',
  'app.tagline': 'Una historia interactiva con IA, local y privada.',

  'nav.home': 'Mundos',
  'nav.settings': 'Ajustes',
  'nav.back': 'Volver',

  'common.loading': 'Cargando…',
  'common.retry': 'Reintentar',
  'common.cancel': 'Cancelar',
  'common.create': 'Crear',
  'common.creating': 'Creando…',
  'common.optional': 'opcional',
  'common.language': 'Idioma',
  'common.error': 'Algo ha salido mal',

  'home.heading': 'Tus mundos',
  'home.empty': 'Todavía no hay mundos. Crea uno para empezar.',
  'home.createWorld': 'Crear un mundo',
  'home.continue': 'Continuar partida',
  'home.sessionsEmpty': 'Todavía no hay partidas guardadas.',
  'home.openWorld': 'Abrir mundo',

  'world.form.name': 'Nombre del mundo',
  'world.form.namePlaceholder': 'La Corona Rota',
  'world.form.genre': 'Género',
  'world.form.genrePlaceholder': 'Fantasía / anime / aventura',
  'world.form.setting': 'Ambientación',
  'world.form.settingPlaceholder': 'Una capital amurallada cuyas defensas están fallando.',
  'world.form.description': 'Premisa',
  'world.form.descriptionPlaceholder': 'El jugador despierta en una ciudad al borde de una crisis.',
  'world.form.language': 'Idioma de la historia',
  'world.form.languageHelp':
    'El idioma en el que se escribe la historia. No se puede cambiar una vez creado el mundo.',
  'world.form.preset': 'Estilo del mundo',
  'world.form.presetHelp':
    'Cuán duro, esperanzador y rápido es este mundo. Se elige una vez, al crearlo.',

  'rules.heading': 'Reglas del mundo',
  'rules.field.danger': 'Peligro',
  'rules.field.lethality': 'Letalidad',
  'rules.field.plotArmor': 'Protección del guion',
  'rules.field.consequences': 'Consecuencias',
  'rules.field.progression': 'Progresión',
  'rules.field.simulation': 'Reloj del mundo',
  'rules.field.enforcement': 'Reglas',

  'rules.level.veryLow': 'Muy baja',
  'rules.level.low': 'Baja',
  'rules.level.moderate': 'Moderada',
  'rules.level.high': 'Alta',
  'rules.level.veryHigh': 'Muy alta',

  'rules.pace.off': 'Ninguna',
  'rules.pace.verySlow': 'Muy lenta',
  'rules.pace.slow': 'Lenta',
  'rules.pace.medium': 'Media',
  'rules.pace.fast': 'Rápida',
  'rules.pace.animeFast': 'Explosiva',

  'rules.time.paused': 'En pausa',
  'rules.time.actionBased': 'Avanza contigo',
  'rules.time.active': 'Siempre en marcha',

  'rules.enforcement.cinematic': 'Cinematográfico',
  'rules.enforcement.flexible': 'Flexible',
  'rules.enforcement.strict': 'Estricto',
  'rules.enforcement.simulationist': 'Simulacionista',

  'rules.preset.balanced': 'Equilibrado',
  'rules.preset.cozyFantasy': 'Fantasía acogedora',
  'rules.preset.shonen': 'Shonen',
  'rules.preset.darkFantasy': 'Fantasía oscura',
  'rules.preset.simulationist': 'Simulacionista',
  'rules.preset.isekaiPowerFantasy': 'Isekai de poder',

  'world.characters': 'Personajes',
  'world.charactersEmpty': 'Todavía no hay personajes. Añade uno para que el mundo tenga vida.',
  'world.addCharacter': 'Añadir personaje',
  'world.startSession': 'Empezar una partida',
  'world.sessions': 'Partidas guardadas',

  'character.form.name': 'Nombre',
  'character.form.personality': 'Personalidad',
  'character.form.personalityPlaceholder': 'inteligente, sarcástica, cautelosa',
  'character.form.appearance': 'Apariencia',
  'character.form.description': 'Descripción',
  'character.form.speechStyle': 'Forma de hablar',
  'character.form.backstory': 'Trasfondo',

  'session.form.title': 'Nombre de la partida',
  'session.form.playerName': 'Nombre de tu personaje',
  'session.form.playerDescription': '¿Quién eres?',
  'session.form.location': 'Lugar inicial',
  'session.turn': 'Turno {n}',
  'session.empty': 'La historia aún no ha empezado. Escribe qué haces primero.',
  'session.inputPlaceholder': 'Escribe lo que quieras…',
  'session.send': 'Enviar',
  'session.sending': 'La historia se desarrolla…',
  'session.suggestions': 'Acciones sugeridas',
  'session.turnFailed': 'No se ha podido generar ese turno.',
  'session.turnFailedRetry': 'No se ha guardado nada. Puedes intentarlo de nuevo.',
  'session.narrator': 'Narrador',
  'session.you': 'Tú',
  'session.time.label': 'Hora en la historia',
  'session.time.dawn': 'amanecer',
  'session.time.morning': 'mañana',
  'session.time.afternoon': 'tarde',
  'session.time.evening': 'atardecer',
  'session.time.night': 'noche',
  'session.time.lateNight': 'madrugada',

  'settings.heading': 'Ajustes',
  'settings.uiLanguage': 'Idioma de la interfaz',
  'settings.uiLanguageHelp': 'Afecta solo a esta interfaz, no al idioma de tus historias.',
  'settings.aiStatus': 'Estado de la IA',
  'settings.storyProvider': 'Proveedor de historia',
  'settings.imageProvider': 'Proveedor de imágenes',
  'settings.model': 'Modelo',
  'settings.configuredVia':
    'Los proveedores se configuran con variables de entorno en el ordenador.',

  'status.ready': 'Listo',
  'status.unreachable': 'Inaccesible',
  'status.misconfigured': 'Requiere configuración',
  'status.disabled': 'Desactivado',
  'status.offline': 'Backend inaccesible',
  'status.offlineHelp': 'La app no puede contactar con la API. ¿Está el backend en marcha?',
};

export const MESSAGES: Record<UiLocale, Record<MessageKey, string>> = { en, es };
