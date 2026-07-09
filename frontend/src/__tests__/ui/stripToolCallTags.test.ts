import { stripToolCallTags } from '@/components/ui/MarkdownRenderer'

describe('stripToolCallTags', () => {
  it('leaves plain reasoning prose untouched', () => {
    const text = 'Bây giờ tôi sẽ kiểm tra workspace hiện tại để xem có dữ liệu nào chưa.'
    expect(stripToolCallTags(text)).toBe(text)
  })

  it('strips XML <invoke> blocks but keeps surrounding prose', () => {
    const text =
      'I will write it.\n<invoke>{"name":"write_file"}</invoke>\nDone.'
    const out = stripToolCallTags(text)
    expect(out).toContain('I will write it.')
    expect(out).toContain('Done.')
    expect(out).not.toContain('invoke')
  })

  it('truncates at an ASCII pipe-wrapped token <|tool_call|>', () => {
    const text = 'Let me check the files.\n<|tool_call|>\n{"name":"list_files"}'
    expect(stripToolCallTags(text)).toBe('Let me check the files.')
  })

  it('truncates at a fullwidth pipe-wrapped token (U+FF5C)', () => {
    const text =
      'Bây giờ tôi sẽ kiểm tra workspace:\n<｜DSML｜function_calls{"x":1}'
    expect(stripToolCallTags(text)).toBe('Bây giờ tôi sẽ kiểm tra workspace:')
  })

  it('truncates at a DeepSeek-style begin token', () => {
    const text =
      'Tôi sẽ liệt kê thư mục.\n<｜tool▁calls▁begin｜><｜tool▁call▁begin｜>function<｜tool▁sep｜>list_files'
    expect(stripToolCallTags(text)).toBe('Tôi sẽ liệt kê thư mục.')
  })

  it('does not touch a bare word like function_calls in prose', () => {
    const text = 'I will make several function_calls to the API.'
    expect(stripToolCallTags(text)).toBe(text)
  })
})
