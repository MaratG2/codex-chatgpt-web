"""Require the owner's real visual template for every AI Education ad gallery."""
from hashlib import sha256
from pathlib import Path
import json
import re

REPO = Path('/home/codexweb/experiment/workspaces/ai-education')
MANIFEST = REPO / 'branding/gallery-template.json'


def prepare(destination, slot):
    if not destination.is_relative_to(REPO / 'listings/avito/drafts'):
        return {}
    if not MANIFEST.is_file():
        raise ValueError('STYLE_REFERENCE_MISSING: approved gallery manifest is absent; do not generate a new style')
    manifest = json.loads(MANIFEST.read_text())
    if manifest.get('status') != 'READY' or manifest.get('owner_approved') is not True:
        raise ValueError('STYLE_REFERENCE_MISSING: original first-course template assets are not ready; preserve existing files and continue other eligible work')
    slots = manifest['slots']
    if slot not in slots:
        raise ValueError('STYLE_SLOT_REQUIRED: choose template_slot from the approved six-slot gallery manifest')
    selected = slots[slot]
    if destination.name != selected['output_filename']:
        raise ValueError('STYLE_SLOT_FILENAME_MISMATCH: use the canonical filename for this template slot')
    references = []
    # Require the entire approved set to be real before claiming a shared template.
    # Feed the corresponding slide plus the cover's common identity into GPT Image.
    for key, entry in slots.items():
        path = REPO / entry['reference_path']
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(REPO.resolve()):
            raise ValueError('STYLE_REFERENCE_MISSING: approved template PNG is unavailable: ' + key)
        digest = sha256(path.read_bytes()).hexdigest()
        if not re.fullmatch(r'[a-f0-9]{64}', entry.get('sha256', '')) or digest != entry['sha256']:
            raise ValueError('STYLE_REFERENCE_CHANGED: template hash is absent or does not match: ' + key)
        if key in ('01', slot):
            references.append({'path': str(path.resolve()), 'sha256': digest, 'slot': key})
    return {'template_id': manifest['template_id'], 'template_slot': slot,
            'template_mode': selected['mode'],
            'reuse_source': str((REPO / selected['reference_path']).resolve()),
            'reuse_sha256': selected['sha256'],
            'reference_images': references,
            'style_instruction': 'Use the attached owner-approved course gallery as a strict visual template. '
            'Make a minimal edit of the corresponding reference slide, preserving its layout, palette, typography hierarchy, proportions, margins, '
            'author appearance when present, visual rendering style and information blocks. '
            'Keep the same six-slide structure, studio background, large white/cyan headings and cyan CTA. Replace only the course-specific topic, truthful copy and '
            'relevant subject matter. Do not invent a new art direction, 3D still-life layout or generic UI cards. '
            'The attached approved template takes precedence over contradictory stylistic wording below. '
            'Do not copy another course price, review or service claim unless approved for this course.'}


def validate_call(record, args):
    references = record.get('reference_images', [])
    if not references:
        return
    supplied = args.get('referenced_image_paths')
    expected = [item['path'] for item in references]
    if not isinstance(supplied, list) or sorted(supplied) != sorted(expected):
        raise ValueError('STYLE_REFERENCE_NOT_USED: generator must receive the exact referenced_image_paths from prepare')
    if args.get('num_last_images_to_include'):
        raise ValueError('STYLE_REFERENCE_NOT_USED: recent chat images cannot replace the approved file references')
    for item in references:
        path = Path(item['path'])
        if path.is_symlink() or sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('STYLE_REFERENCE_CHANGED: source changed after prepare')
