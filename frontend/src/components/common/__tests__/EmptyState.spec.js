import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import EmptyState from '../EmptyState.vue'

describe('EmptyState', () => {
  it('renders the title and description', () => {
    const wrapper = mount(EmptyState, {
      props: {
        title: 'No projects yet',
        description: 'A project holds a contract and the claims raised under it.',
      },
    })

    expect(wrapper.text()).toContain('No projects yet')
    expect(wrapper.text()).toContain('A project holds a contract')
  })

  it('omits the description paragraph entirely when none is given', () => {
    const wrapper = mount(EmptyState, { props: { title: 'Nothing here' } })
    expect(wrapper.find('.empty__description').exists()).toBe(false)
  })

  it('renders no action buttons by default', () => {
    const wrapper = mount(EmptyState, { props: { title: 'Nothing here' } })
    expect(wrapper.find('.empty__actions').exists()).toBe(false)
    expect(wrapper.findAll('button')).toHaveLength(0)
  })

  it('emits action when the primary button is clicked', async () => {
    const wrapper = mount(EmptyState, {
      props: { title: 'No documents', actionLabel: 'Upload' },
    })

    await wrapper.get('.empty__button--primary').trigger('click')

    expect(wrapper.emitted('action')).toHaveLength(1)
    expect(wrapper.emitted('secondary-action')).toBeUndefined()
  })

  it('emits secondary-action separately from action', async () => {
    const wrapper = mount(EmptyState, {
      props: {
        title: 'No matches',
        actionLabel: 'Upload',
        secondaryActionLabel: 'Clear filters',
      },
    })

    const buttons = wrapper.findAll('.empty__button')
    expect(buttons).toHaveLength(2)

    await buttons[1].trigger('click')

    expect(wrapper.emitted('secondary-action')).toHaveLength(1)
    expect(wrapper.emitted('action')).toBeUndefined()
  })

  it('uses the supplied icon class', () => {
    const wrapper = mount(EmptyState, {
      props: { title: 'Locked', icon: 'pi pi-lock' },
    })
    expect(wrapper.find('.empty__icon i').classes()).toContain('pi-lock')
  })

  it('renders a hint below the actions when given', () => {
    const wrapper = mount(EmptyState, {
      props: { title: 'No projects', hint: 'Requires the “Manage projects” permission.' },
    })
    expect(wrapper.get('.empty__hint').text()).toContain('Manage projects')
  })

  it('renders default slot content', () => {
    const wrapper = mount(EmptyState, {
      props: { title: 'Pending' },
      slots: { default: '<p class="custom">Will read from /api/v1/claims/</p>' },
    })
    expect(wrapper.get('.custom').text()).toContain('/api/v1/claims/')
  })

  it('lets the actions slot replace the built-in buttons', () => {
    const wrapper = mount(EmptyState, {
      props: { title: 'Pending', actionLabel: 'Ignored' },
      slots: { actions: '<button class="slotted">Custom</button>' },
    })

    expect(wrapper.find('.slotted').exists()).toBe(true)
    expect(wrapper.find('.empty__button--primary').exists()).toBe(false)
  })

  it('applies the compact modifier only when asked', () => {
    expect(mount(EmptyState, { props: { title: 'x' } }).classes()).not.toContain('empty--compact')
    expect(
      mount(EmptyState, { props: { title: 'x', compact: true } }).classes()
    ).toContain('empty--compact')
  })
})
