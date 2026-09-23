ALTER TABLE page_stage_state
    DROP CONSTRAINT IF EXISTS page_stage_state_stage_check;
ALTER TABLE page_stage_state
    ADD CONSTRAINT page_stage_state_stage_check CHECK (stage IN (
        'input', 'colorization', 'upscale', 'detection', 'ocr', 'bubble_detection',
        'text_grouping', 'translation', 'mask_generation', 'layout',
        'inpainting', 'rendering', 'finalize'
    ));

ALTER TABLE page_stage_attempts
    DROP CONSTRAINT IF EXISTS page_stage_attempts_stage_check;
ALTER TABLE page_stage_attempts
    ADD CONSTRAINT page_stage_attempts_stage_check CHECK (stage IN (
        'input', 'colorization', 'upscale', 'detection', 'ocr', 'bubble_detection',
        'text_grouping', 'translation', 'mask_generation', 'layout',
        'inpainting', 'rendering', 'finalize'
    ));

ALTER TABLE pipeline_documents
    DROP CONSTRAINT IF EXISTS pipeline_documents_stage_check;
ALTER TABLE pipeline_documents
    ADD CONSTRAINT pipeline_documents_stage_check CHECK (stage IN (
        'input', 'colorization', 'upscale', 'detection', 'ocr', 'bubble_detection',
        'text_grouping', 'translation', 'mask_generation', 'layout',
        'inpainting', 'rendering', 'finalize'
    ));

ALTER TABLE pipeline_artifacts
    DROP CONSTRAINT IF EXISTS pipeline_artifacts_stage_check;
ALTER TABLE pipeline_artifacts
    ADD CONSTRAINT pipeline_artifacts_stage_check CHECK (stage IN (
        'input', 'colorization', 'upscale', 'detection', 'ocr', 'bubble_detection',
        'text_grouping', 'translation', 'mask_generation', 'layout',
        'inpainting', 'rendering', 'finalize'
    ));
