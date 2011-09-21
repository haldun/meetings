// Dropdown menus
$("body").bind("click", function (e) {
  $('.dropdown-toggle, .menu').parent("li").removeClass("open");
});
$(".dropdown-toggle, .menu").click(function (e) {
  var $li = $(this).parent("li").toggleClass('open');
  return false;
});

// Generic tornado stuff
function getCookie(name) {
  var r = document.cookie.match("\\b" + name + "=([^;]*)\\b");
  return r ? r[1] : undefined;
}

// Home page js
(function(){
  if (!$(document.body).hasClass('home')) {
    return;
  }

  $.ajaxSetup({
    data: {_xsrf: getCookie('_xsrf')}
  });

  $('tr.room').delegate('a.danger', 'click', function(e){
    e.preventDefault();
    var self = $(this);
    if (confirm('Are you sure to delete this room? All the messages and files in this room will also be deleted')) {
      $.post("/rooms/" + self.data('room') + "/delete", function(){
        self.parents('tr.room').fadeOut();
      });
    }
  });
})();

// Room page js
(function(){
  if (!window.M) {
    return;
  }

  $('#room-menu a').removeClass('active');
  $('#room-menu a.' + M.active_menu).addClass('active');

  // Sounds
  var sounds = {
    'new': new MediaElement('snd_new'),
    'on': new MediaElement('snd_on'),
    'off': new MediaElement('snd_off')
  };

  var $form = $('#new_message');
  var $compose = $form.find('textarea');

  var messageToDom = function(message) {
    var $tr = $('<tr>');

    $tr.append($('<td>').addClass('user').text(message.user_name));

    var $content = $('<td>');

    if (message.type === 'image') {
      var $a = $('<a>').attr({href: message.url, target: '_blank'});
      $a.append($('<img>').attr('src', message.thumb_url));
      $content.append($a);
    } else if (message.type === 'file') {
      $content.append($('<a>').attr({href: message.url, target: '_blank'}).text(message.url));
    } else if (message.type === 'text') {
      $content.text(message.content);
      // $content.html(linkify(message.content));
    } else if (message.type === 'topic_changed') {
      $content.text('changed topic to ' + message.content);
    } else if (message.type === 'presence') {
      $content.text('entered the room');
    } else if (message.type === 'leave') {
      $content.text('left the room');
    }

    $tr.append($content);

    $('#no-messages').fadeOut('slow');

    return $tr;
  };

  PUBNUB.subscribe({
    channel: M.room.token,
    error: function() {
      // alert("Connection lost.");
    },
    callback: function(message) {
      if (message.type == 'image' || message.type == 'file') {
        $('#messages').append(messageToDom(message));
        scroll_page();
        sounds['new'].play();
      }

      if (message.type == 'text' && message.user_id !== M.current_user.id) {
        $('#messages').append(messageToDom(message));
        scroll_page();
        sounds['new'].play();
      }

      if (message.type == 'presence' && message.user_id !== M.current_user.id) {
        var id = 'user_' + message.user_id;
        if ($('#' + id).length === 0) {
          var el = $('<li>').attr('id', id).text(message.user_name);
          $('#room-users').append(el);
          $('#messages').append(messageToDom(message));
          sounds['on'].play();
        }
      }

      if (message.type === 'leave') {
        $('#user_' + message.user_id).fadeOut('slow').remove();
        $('#messages').append(messageToDom(message));
        sounds['off'].play();
      }

      if (message.type === 'topic_changed') {
        $('#topic').text(message.content);
        $('#messages').append(messageToDom(message));
      }
    },
    connect: function() {}
  });

  $form.submit(function(e){
    var $this = $(this), url = $this.attr('action');
    $('#messages').append(messageToDom({
      user_name: M.current_user.name,
      content: $compose.val(),
      type: 'text'
    }));
    scroll_page();
    $.post(url, $this.serialize(), function(){
    });
    $this[0].reset();
    e.preventDefault();
  });

  var NOT_WRITING = 0,
      WRITING = 1,
      STOPPED_WRITING = 2;
  var prev_state = NOT_WRITING, compose_state = NOT_WRITING;

  $compose.keypress(function(e) {
    var code = (e.keyCode ? e.keyCode : e.which);
    if (code === 13) {
      e.preventDefault();

      if (uploader.total.queued > 0) {
        uploader.start();
        $(this).val('');
        // uploader.files = [];
      } else {
        if ($(this).val() !== '') {
          $form.submit();
        }
      }
    }
  }).keyup(function(e) {
    compose_state = WRITING;
  });

  // var status_interval = setInterval( function(){
  //   if (compose_state === NOT_WRITING) {
  //     if (prev_state === NOT_WRITING) {
  //       // do nothing
  //     } else if (prev_state === WRITING) {
  //       console.log("stopped writing");
  //     }      
  //   } else if (compose_state === WRITING) {
  //     console.log("writing");
  //   } else if (compose_state === STOPPED_WRITING) {
  //     console.log("stopped writing");
  //   }
  // }, 500);

  var scroll_page = function() {
    $('html, body').animate({scrollTop: $(document).height()}, 'slow');
  };

  $('#messages').find('tr.text td').each(function(i, el) {
    // el.innerHTML = linkify(el.innerHTML);
  });

  if ($('#messages').length > 0) {
    setTimeout(scroll_page, 50);
  }
  $compose.focus();

  var getCookie = function(name) {
    var r = document.cookie.match("\\b" + name + "=([^;]*)\\b");
    return r ? r[1] : undefined;
  };

  // Uploader
  window.uploader = new plupload.Uploader({
      runtimes: 'html5,flash',
      browse_button: 'select_files',
      container: 'upload_container',
      max_file_size: '10mb',
      url: '/rooms/' + M.room.id + '/upload',
      flash_swf_url: '/static/javascripts/plupload.flash.swf',
      filters: [],
      multipart: true,
      multipart_params: {
        '_xsrf': getCookie('_xsrf'),
        'auth_token': getCookie('auth_token')
      },
      drop_element: 'text_content'
    });

    uploader.bind('Init', function(up, params) {});

    uploader.bind('FilesAdded', function(up, files) {
      var val = $('#text_content').val();
      $.each(files, function(i, file) {
        val += file.name + ' ' + plupload.formatSize(file.size) + '\n';
        // $('#filelist').append(
        //   $('<div>').attr('id', file.id).text(
        //       file.name + ' (' + plupload.formatSize(file.size) + ')')
        //   .append('<b>')
        // );
      });
      $('#text_content').val(val);
      up.refresh();
      $('#upload_buttons').show();
      $('#upload').show();
    });

    uploader.bind('UploadProgress', function(up, file) {
      $('#' + file.id + " b").html(file.percent + "%");
    });

    uploader.bind('Error', function(up, err) {
      $('#filelist').append("<div>Error: " + err.code +
        ", Message: " + err.message +
        (err.file ? ", File: " + err.file.name : "") +
        "</div>"
      );
      up.refresh();
    });

    uploader.bind('FileUploaded', function(up, file) {
      // $('#' + file.id + " b").html("100%");
      $('#upload').text('Upload files').removeClass('disabled').hide();
    });

    $('#upload').click(function(e) {
      uploader.start();
      $compose.val('');
      $(this).text('Uploading...').addClass('disabled');
      e.preventDefault();
    });

    uploader.init();

  $('#room-menu a:not(.leave)').pjax('#content').live('click', function(){
    $('#room-menu a').removeClass('active');
    $(this).addClass('active');
  })

  $(document.body).bind('end.pjax', function(xhr){
    $('#messages').find('tr.text td').each(function(i, el) {
      //el.innerHTML = linkify(el.innerHTML);
    });
    $compose.focus();
    if ($('#messages').length > 0) {
      setTimeout(scroll_page, 50);
    }
  });
})();
