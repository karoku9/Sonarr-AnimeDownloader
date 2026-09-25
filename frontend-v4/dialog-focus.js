/** Keep Tab/Shift+Tab within a native modal, including boundary wrap. */
export function trapDialogTab(event,dialog) {
 if(event.key!=='Tab')return;
 const controls=[...dialog.querySelectorAll('button,input,select,textarea,[tabindex]')].filter(control=>!control.disabled&&control.tabIndex>=0&&!control.closest('[hidden]'));
 if(!controls.length){event.preventDefault();dialog.focus();return;}
 const first=controls[0],last=controls.at(-1),active=dialog.ownerDocument.activeElement;
 if(event.shiftKey&&active===first){event.preventDefault();last.focus();}
 else if(!event.shiftKey&&active===last){event.preventDefault();first.focus();}
}
