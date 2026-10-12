<#--
  Copyright (c) 2026, MariaDB plc.

  This program is free software; you can redistribute it and/or modify
  it under the terms of the GNU General Public License, version 2.0,
  as published by the Free Software Foundation.

  This program is distributed in the hope that it will be useful, but
  WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
  the GNU General Public License, version 2.0, for more details.

  You should have received a copy of the GNU General Public License
  along with this program; if not, write to the Free Software Foundation, Inc.,
  51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA
-->
<#-- The sign-in page: the base theme's login.ftl with the MCP server's joined
     user name and password fields and round "next" button. -->
<#import "template.ftl" as layout>
<#import "passkeys.ftl" as passkeys>
<@layout.registrationLayout displayMessage=!messagesPerField.existsError('username','password') displayInfo=realm.password && realm.registrationAllowed && !registrationDisabled??; section>
    <#if section = "header">
        ${msg("loginAccountTitle")}
    <#elseif section = "form">
        <#if realm.password>
            <#assign usernameLabel><#if !realm.loginWithEmailAllowed>${msg("username")}<#elseif !realm.registrationEmailAsUsername>${msg("usernameOrEmail")}<#else>${msg("email")}</#if></#assign>
            <form id="kc-form-login" class="mrsLoginFields" onsubmit="login.disabled = true; return true;" action="${url.loginAction}" method="post">
                <#if !usernameHidden??>
                    <div class="mrsLoginField">
                        <input id="username" name="username" type="text" value="${(login.username!'')}"
                               placeholder="${usernameLabel}" aria-label="${usernameLabel}" dir="ltr"
                               autocomplete="${(enableWebAuthnConditionalUI?has_content)?then('username webauthn', 'username')}"
                               aria-invalid="<#if messagesPerField.existsError('username','password')>true</#if>"
                               <#if !((login.username!"")?has_content)>autofocus</#if>>
                    </div>
                </#if>
                <div class="mrsLoginField">
                    <input id="password" name="password" type="password"
                           placeholder="${msg("password")}" aria-label="${msg("password")}" autocomplete="current-password"
                           aria-invalid="<#if messagesPerField.existsError('username','password')>true</#if>"
                           <#if usernameHidden?? || (login.username!"")?has_content>autofocus</#if>>
                    <button type="submit" name="login" id="kc-login" class="mrsLoginBtnNext"
                            aria-label="${msg("doLogIn")}" title="${msg("doLogIn")}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 7.5l4.5 4.5L9 16.5"/></svg></button>
                </div>
                <input type="hidden" id="id-hidden-input" name="credentialId" <#if auth.selectedCredential?has_content>value="${auth.selectedCredential}"</#if>/>
                <#if realm.rememberMe && !usernameHidden??>
                    <label class="mcpRememberMe"><input id="rememberMe" name="rememberMe" type="checkbox" <#if login.rememberMe??>checked</#if>> ${msg("rememberMe")}</label>
                </#if>
            </form>

            <#if messagesPerField.existsError('username','password')>
                <div class="mrsLoginError" role="alert" aria-live="polite"><p>${kcSanitize(messagesPerField.getFirstError('username','password'))?no_esc}</p></div>
            </#if>

            <#if realm.resetPasswordAllowed>
                <a class="mcpLinks" href="${url.loginResetCredentialsUrl}">${msg("doForgotPassword")}</a>
            </#if>
        </#if>
        <@passkeys.conditionalUIData />
    <#elseif section = "info">
        <#if realm.password && realm.registrationAllowed && !registrationDisabled??>
            ${msg("noAccount")} <a href="${url.registrationUrl}">${msg("doRegister")}</a>
        </#if>
    <#elseif section = "socialProviders">
        <#if realm.password && social?? && social.providers?has_content>
            <div id="kc-social-providers" class="mcpSocial">
                <p>${msg("identity-provider-login-label")}</p>
                <ul>
                    <#list social.providers as p>
                        <li><a data-once-link id="social-${p.alias}" class="mcpSocialButton" href="${p.loginUrl}">${p.displayName!}</a></li>
                    </#list>
                </ul>
            </div>
        </#if>
    </#if>
</@layout.registrationLayout>
